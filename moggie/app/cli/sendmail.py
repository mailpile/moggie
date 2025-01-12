import asyncio
import base64
import json
import logging
import re
import shlex
import ssl
import sys
import shlex
import time

import aiosmtplib
import aiosmtplib.smtp
from aiosmtplib.response import SMTPResponse
from aiosmtplib.protocol import SMTPProtocol

from ...api.requests import *
from ...config import AccessConfig
from ...email.metadata import Metadata
from ...util.friendly import friendly_time_to_seconds
from .command import Nonsense, CLICommand
from .annotate import CommandAnnotate


class LoggingSMTPProtocol(SMTPProtocol):
    def write(self, data: bytes) -> None:
        super().write(data)
        if len(data) > 70:
            data = data[:70] + b'...'
        for line in str(data, 'utf-8').splitlines():
            if line.startswith('AUTH '):
                line = line[:11] + '<<SECRETS...>>'
            logging.debug('>> %s' % line)

    def data_received(self, data: bytes) -> None:
        for line in str(data, 'utf-8').splitlines():
            logging.debug('<< %s' % line)
        super().data_received(data)


def enable_smtp_logging():
    #asyncio.get_event_loop().set_debug(True)
    al = logging.getLogger('asyncio')
    al.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    al.addHandler(ch)

    # Monkey patch this, because they don't provide hooks. :-(
    aiosmtplib.smtp.SMTPProtocol = LoggingSMTPProtocol


class ServerAndSender:
    PROTO_SMTP_CLEARTEXT = 'smtpclr'
    PROTO_SMTP_BEST_EFFORT = 'smtp'
    PROTO_SMTP_STARTTLS = 'starttls'
    PROTO_SMTP_OVER_TLS = 'smtps'

    PROTOS = set(['smtp', 'smtpclr', 'smtptls', 'smtps'])

    PORT_SMTP = 25
    PORT_SMTPS = 465

    def __init__(self, server=None, sender=None, key=None):
        self.proto = self.PROTO_SMTP_BEST_EFFORT
        self.auth = None
        self.host = None
        self.port = None
        self.sender = sender
        if key:
            self.parse_key(key)
        if server:
            self.parse_server_spec(server)

    use_tls = property(lambda s: (s.proto == s.PROTO_SMTP_OVER_TLS))
    use_starttls = property(lambda s: (s.proto in (
        s.PROTO_SMTP_BEST_EFFORT,
        s.PROTO_SMTP_STARTTLS)))

    def __hash__(self):
        return hash('%s/%s/%d/%s'
            % (self.proto, self.host, self.port, self.sender))

    def __str__(self):
        return ('%s/%s/%s/%d/%s'
            % (self.proto, self.host, self.auth or '', self.port, self.sender))

    def parse_key(self, key):
        self.proto, self.host, self.auth, port, self.sender = [
            k.strip() for k in key.split('/', 4)]
        self.port = int(port)
        return self

    def username_and_password(self):
        if self.auth:
            u, p = self.auth.split(',', 1)
            u = str(base64.b64decode(bytes(u.strip(), 'utf-8')), 'utf-8')
            p = str(base64.b64decode(bytes(p.strip(), 'utf-8')), 'utf-8')
            return u, p
        return None, None

    def encode_userpass(self, userpass):
        if ':' in userpass:
            u, p = userpass.split(':', 1)
        else:
            u, p = userpass, ''
        u = str(base64.b64encode(bytes(u, 'utf-8')), 'utf-8')
        p = str(base64.b64encode(bytes(p, 'utf-8')), 'utf-8')
        return '%s,%s' % (u, p)

    def parse_server_spec(self, sspec):
        if '://' in sspec:
            self.proto, sspec = sspec.split('://')

        self.proto = None
        self.port = 0

        parts = [p.strip() for p in sspec.split(':')]
        if len(parts) == 1:
            self.host = parts[0]
        else:
            if parts[0] in self.PROTOS:
                self.proto = parts.pop(0)

            # Attempt to read the port off the end; this allows a
            # variable number of parts as would be expected if the
            # host name is actually an IPv6 address.
            try:
                self.port = int(parts[-1])
                parts.pop(-1)
            except ValueError:
                pass

            # Reassemble IPv6 addresses?
            self.host = ':'.join(parts)

        if '@' in self.host:
            userpass, self.host = self.host.split('@', 1)
            self.auth = self.encode_userpass(userpass)

        if ':' in self.host and not self.host[:1] == '[':
            self.host = '[%s]' % self.host

        if not self.port:
            if self.proto == self.PROTO_SMTP_OVER_TLS:
                self.port = self.PORT_SMTPS
            else:
                self.port = self.PORT_SMTP
        
        if not self.proto:
            if self.port == self.PORT_SMTPS:
                self.proto = self.PROTO_SMTP_OVER_TLS
            else:
                self.proto = self.PROTO_SMTP_BEST_EFFORT

        logging.debug('Parsed %s to %s' % (sspec, self))

        return self


class SendingProgress:
    """
    This is a class which tracks the progress of sending an e-mail via one
    or more servers, to one or more recipients. It includes methods for
    serializing/deserializing its state to/from Metadata annotations.
    """
    PENDING = 'p'
    REJECTED = 'r'  # Permanent errors
    DEFERRED = 'd'  # Temporary errors
    SENT = 's'
    USE_MX = 'MX'

    DEFERRED_BACKOFF = 30 * 60  # Wait at least 30 minutes after errors
    TIMEOUT = 5

    def __init__(self, metadata=None):
        self.status = {}
        self.history = []
        if metadata is not None:
            self.from_annotations(metadata.annotations)

    all_recipients = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()])

    unsent = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()
        if s._is_unsent(status)])

    done = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()
        if s._is_unsent(status)])

    def _is_unsent(self, status):
        return status[-1:] not in (self.SENT, self.REJECTED)

    def _is_ready(self, now, status):
        ts = int(status[:-1], 16)

        # Is sending postponed/scheduled for the future?
        postponed = (ts > now)

        # Are we in a deferred state and tried too recently?
        back_offs = self.DEFERRED_BACKOFF * (1 + len(self.history))
        backing_off = (
            (status[-1:] == self.DEFERRED) and
            (ts - back_offs > now))

        return (not postponed) and (not backing_off) 

    def __str__(self):
        return '<Sending status=%s history=%s>' % (self.status, self.history)

    def get_rcpt_statuses(self):
        for ss_pair, rstats in self.status.items():
            for recipient, status in rstats.items():
                yield (recipient, status)

    def rcpt(self, server, sender, *recipients, ts=0):
        # FIXME: Fix formatting of SMTP server spec or raise if nonsense
        ss = ServerAndSender(server, sender)
        s = self.status[ss] = self.status.get(ss, {})
        s.update(dict((r, '%x%s' % (ts, self.PENDING)) for r in recipients))
        return self

    def progress(self, status, server_and_sender, *recipients, ts=None, log=None):
        ts = int(time.time()) if (ts is None) else ts
        ss = server_and_sender
        for recipient in recipients:
            s = self.status[ss] = self.status.get(ss, {})
            s[recipient] = '%x%s' % (ts, status)
        if log:
            logging.debug('progress(%s -> %s): %s'
                % (server_and_sender, recipients, log))
            self.history.append((ts, log))
        return self

    def from_annotations(self, annotations):
        for key, val in annotations.items():
            try:
                if key.startswith('=send/'):
                    ss = ServerAndSender(key=key[6:])
                    stats = dict(v.split('=', 1) for v in val.split(' '))
                    self.status[ss] = stats
                elif key.startswith('=slog/'):
                    self.history.append((int(key[6:], 16), val))
            except (ValueError, KeyError, IndexError):
                pass
        self.history.sort()
        return self

    def as_annotations(self):
        annotations = {}
        for ss, stats in self.status.items():
            status = ' '.join('%s=%s' % (r, s) for r, s in stats.items())
            annotations['=send/%s' % ss] = status
        for ts, line in self.history:
            annotations['=slog/%x' % ts] = '%s' % line
        return annotations

    async def attempt_send(progress, sending_email,
            timeout=TIMEOUT,
            send_at=None,
            now=None):
        """
        Attempt to connect to all the mail servers we have recipients for,
        attempt to send and update our state in the process. Returns True
        if anything at all changed, False otherwise.
        """
        now = int(time.time()) if (now is None) else now

        if send_at is not None:
            made_changes = progress.update_unsent_timestamps(send_at)
        else:
            made_changes = False

        for ss, stats in progress.status.items():
            rcpts = [
                r for r, s in stats.items()
                if progress._is_unsent(s) and progress._is_ready(now, s)]
            if rcpts:
                if await progress.smtp_send(sending_email, ss, rcpts, timeout):
                    made_changes = True

        return made_changes

    def update_unsent_timestamps(progress, new_ts):
        # Iterate through the plan and add new progress events with the
        # requested timestamp.
        updates = []
        for ss, stats in progress.status.items():
            for rcpt, stat in stats.items():
                if progress._is_unsent(stat):
                    updates.append((stat[-1:], ss, rcpt))
        for update in updates:
            progress.progress(*update, ts=new_ts)
        return bool(updates)

    async def smtp_send(progress, sending_email, ss, recipients, timeout=TIMEOUT):
        enable_smtp_logging()

        try:
            smtp_client = aiosmtplib.SMTP(
                hostname=ss.host, 
                port=ss.port,
                use_tls=ss.use_tls,
                start_tls=ss.use_starttls,
                timeout=timeout)
        except:
            logging.exception('Failed to create smtp_client(%s)' % ss)
            return False

        try:
            async with smtp_client:
                # FIXME: Login if we have credentials
                errors = response = None
                if ss.auth:
                    u, p = ss.username_and_password()
                    response = await smtp_client.login(u, p, timeout=timeout)
                    if not (200 <= response.code < 300):
                        errors = dict(
                            (r, (response.code, response.message))
                            for r in recipients)

                if not errors:
                    errors, response = await smtp_client.sendmail(
                        ss.sender, recipients, sending_email)

                for rcpt in recipients:
                    if rcpt in errors:
                        ecode, msg = errors[rcpt]
                        if ecode < 500:
                            status = progress.DEFERRED
                        else:
                            status = progress.REJECTED
                    else:
                        ss.auth = None
                        msg = response
                        status = progress.SENT
                    progress.progress(status, ss, rcpt, log=msg)

        except aiosmtplib.errors.SMTPException as e:
            progress.progress(progress.DEFERRED, ss, *recipients, log=e)

        except (IOError, OSError, ssl.SSLCertVerificationError) as e:
            progress.progress(progress.DEFERRED, ss, *recipients, log=e)

        try:
            smtp_client.close()
        except (IOError, OSError):
            pass

        return True


class CommandSend(CommandAnnotate):
    """moggie send [options] <search terms>

    Attempt to send one or more messages using SMTP.

    Messages can be loaded using moggie searches, provided as raw files, or
    loaded (one per invocation) from standard input.

    Upon success or failure, if the message exists in Moggie's metadata index,
    a record of progress made will be written to the e-mail's metadata using
    `moggie annotate`.

    ### Examples

        # Parse the To, Cc and Bcc headers and send via a specific server
        $ moggie send --use-headers --send-via=user@server /path/to/file.eml

        # Try to send everything in the outbox, mutating tags when complete.
        # Only messages that have valid sending annotations will be processed.
        $ moggie send in:outbox --tag-sent=-outbox --tag-sent=+sent

    ### Options

    %(OPTIONS)s

    ### Sending rules

    The recipient(s) are determined in the following order:

       1. Specified in command-line options, or
       2. Parsed from the message header iff `--use-headers` is true, or
       3. Extracted from metadata annotations

    The SMTP server used is determined using these rules:

       1. Specified in command-line options, or
       2. Using DNS of recipient domains iff `--direct-smtp` is true, or
       3. Extracted from metadata annotations

    Note that the moggie configuration is never consulted directly, use
    `moggie plan` to generate command line arguments using app settings.
    """
    NAME = 'send'
    ROLES = (AccessConfig.GRANT_READ
            + AccessConfig.GRANT_COMPOSE + AccessConfig.GRANT_NETWORK)

    OPTIONS = CommandAnnotate.OPTIONS + [[
        (None, None, 'sending'),
        ('--tag-sending=',      [], 'X=Tag to add/remove from emails in flight'),
        ('--tag-sent=',         [], 'X=Tag to add/remove from sent messages'),
        ('--send-to=',          [], 'X=Address to send to (ignores headers)'),
        ('--send-from=',    [None], 'X=Address to send as (ignores headers)'),
        ('--send-at=',     ['NOW'], 'X=(NOW*|+seconds|@timestamp)'),
        ('--send-via=',         [], 'X=(smtp|smtps)://[user:pass@]host:port'),
        ('--direct-smtp',  [False], 'Send directly via recipient SMTP servers'),
        ('--use-headers',  [False], 'Scan headers for from/to/cc/bcc'),
        ('--use-tor=',     [False], 'X=host:port, connect via Tor'),
        ('--ignore-all',   [False], 'Ignore/override existing sending plan and state'),
        ('--ignore-ts',    [False], 'Ignore/override existing timestamps'),
        ]]

    # These are just the most critical, known-to-be-private/internal headers.
    SANITIZE_HEADER = re.compile(b'\n'
            b'(Bcc|Tags'
            b'): ([^\n]|\n[ \t])*',
        re.IGNORECASE)

    def __init__(self, *args, **kwargs):
        self.send_at = None
        self.send_via_to = {}
        super().__init__(*args, **kwargs)

    def configure(self, args):
        args = super().configure(args)

        self.options['--entire-thread='][0] = 'false'
        if self.options['--send-via='] and self.options['--direct-smtp'][-1]:
            raise Nonsense('Please use --send-via= or --direct-smtp, not both.')

        send_at = self.options['--send-at='][-1]
        if send_at == 'NOW':
            send_at = 0
        else:
            now = int(time.time())
            if send_at[:1] == '+':
                send_at = now + friendly_time_to_seconds(send_at[1:])
            elif send_at[:1] == '@':
                send_at = int(send_at[1:])
            else:
                send_at = int(send_at)
                if 0 < send_at < 315569520:  # Ten years, ish
                    send_at += now
            if send_at < now:
                raise ValueError('Time %d is in the past!' % send_at)
        self.send_at = max(0, send_at)

        if self.options['--tag-sending=']:
            self.validate_and_normalize_tagops(self.options['--tag-sending='])

        if self.options['--tag-sent=']:
            self.validate_and_normalize_tagops(self.options['--tag-sent='])

        return args

    def _sender_from_header(self, parsed_message):
        try:
            return parsed_message['from']['address']
        except KeyError:
            return None

    def _recipients_from_header(self, parsed_message):
        for hdr in ('to', 'cc', 'bcc'):
            for addr in parsed_message.get(hdr, []):
                addr = addr.get('address')
                if addr:
                    yield addr

    def _sanitize_header(self, raw_message):
        if raw_message[:5] == b'From ':
            raw_message = raw_message[raw_message.index(b'\n')+1:]
        eoh = (b'\r\n' if (b'\r\n' in raw_message) else b'\n') * 2
        hend = raw_message.index(eoh)
        hdrs = b'\n' + raw_message[:hend]
        return self.SANITIZE_HEADER.sub(b'', hdrs)[1:] + raw_message[hend:]

    async def process(self, metadata, parsed_email, raw_message):
        # If we have metadata, check how much progress has been made
        if self.options['--ignore-all'][-1]:
            progress = SendingProgress()
            changed = True
        else:
            progress = SendingProgress(metadata)
            changed = False

        # Figure out to/from, based on arguments or e-mail headers
        sending_from = self.options['--send-from='][-1]
        if not sending_from and self.options['--use-headers'][-1]:
            sending_from = self._sender_from_header(parsed_email)
            if not sending_from:
                raise Nonsense('Failed to extract sender from header')
        sending_to = self.options['--send-to=']
        if not sending_to and self.options['--use-headers'][-1]:
            sending_to.extend(self._recipients_from_header(parsed_email))
            if not sending_to:
                raise Nonsense('Failed to extract recipients from header')

        if (not sending_to) or (sending_to and not sending_from):
            if progress.unsent:
                sending_to = True
            else:
                if progress.done:
                    logging.info('Message already sent, doing nothing')
                    return False
                else:
                    raise Nonsense('Missing sender or recipient(s), unable to send')

        changed = False
        if sending_to is not True:
            if self.options['--direct-smtp'][-1]:
                smtp_server = progress.USE_MX
            elif self.options['--send-via=']:
                smtp_server = self.options['--send-via='][-1]
            else:
                raise Nonsense('Need outgoing SMTP server')

            if not sending_from:
                raise Nonsense('Need from address')

            changed = True
            for addr in sending_to:
                progress.rcpt(smtp_server, sending_from, addr, ts=self.send_at)

        elif sending_from:
            raise Nonsense('Sending address is already defined')

        # Redact any private headers from the e-mail
        clean_message = self._sanitize_header(raw_message)
        in_index = (metadata.idx < 0xffffffff)  # FIXME: Magic number

        # Attempt to send...
        send_at = self.send_at if self.options['--ignore-ts'][-1] else None
        if await progress.attempt_send(clean_message, send_at=send_at):
            changed = True

        if changed:
            annotations = progress.as_annotations()
            if in_index:
                if await self.annotate_messages([metadata.idx], annotations):
                    metadata.more.update(annotations)
                else:
                    changed = False
            else:
                metadata.more.update(annotations)

        # Update message tags as necessary
        if progress.unsent:
            tag_sending = self.options['--tag-sending=']
            if tag_sending and in_index:
                await self.tag_message(metadata, tag_sending)
        else:
            tag_sent = self.options['--tag-sent=']
            if tag_sent and in_index:
                await self.tag_message(metadata, tag_sent)

        return changed

    async def tag_message(self, metadata, tag_ops):
        res = await self.worker.async_api_request(self.access, RequestTag(
            context=self.context,
            tag_ops=[(tag_ops, 'id:%s' % metadata.idx)]))
        logging.debug('Tag %s %s: %s' % (tag_ops, metadata.idx, res))

    async def act_on_results(self, metadatas):
        processed = []
        for md in metadatas:
            query = RequestEmail(
                metadata=md,
                full_raw=True,
                username=self.options['--username='][-1],
                password=self.options['--password='][-1])
            query['context'] = self.context
            res = await self.worker.async_api_request(self.access, query)

            md = Metadata(*md)
            parsed_email = res['email']
            raw_message = base64.b64decode(parsed_email.pop('_RAW'))

            if await self.process(md, parsed_email, raw_message):
                processed.append(md)
            else:
                logging.debug('Not processed: %s' % md.idx)

        return processed
