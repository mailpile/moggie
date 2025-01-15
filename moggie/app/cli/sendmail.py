import asyncio
import base64
import logging
import re
import time

from ...api.requests import *
from ...config import AccessConfig
from ...email.metadata import Metadata
from ...util.friendly import friendly_time_to_seconds
from ...util.sendmail import enable_smtp_logging, ServerAndSender, SendingProgress
from .command import Nonsense, CLICommand
from .annotate import CommandAnnotate


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
        ('--tag-failed=',       [], 'X=Tag to add/remove from failed messages'),
        ('--tag-sent=',         [], 'X=Tag to add/remove from sent messages'),
        ('--send-to=',          [], 'X=Address to send to (ignores headers)'),
        ('--send-from=',    [None], 'X=Address to send as (ignores headers)'),
        ('--send-at=',     ['NOW'], 'X=(NOW*|+seconds|@timestamp)'),
        ('--send-via=',         [], 'X=(@account|(smtp|smtps)://[user:pass@]host:port)'),
        ('--direct-smtp',  [False], 'Send directly via recipient SMTP servers'),
        ('--use-headers',  [False], 'Scan headers for from/to/cc/bcc'),
        ('--use-tor=',     [False], 'X=host:port, connect via Tor'),
        ('--ignore-all',   [False], 'Ignore/override existing sending plan and state'),
        ('--ignore-ts',    [False], 'Ignore/override existing timestamps'),
        ('--retry-now',    [False], 'Retry sending immediately'),
        ('--debug',        [False], 'Enable low level debugging of SMTP dialog'),
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

        if self.options['--retry-now'][-1]:
            self.options['--send-at='].append('NOW')
            self.options['--ignore-ts'].append(True)

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

        if self.options['--tag-failed=']:
            self.validate_and_normalize_tagops(self.options['--tag-failed='])

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

    async def process(self, metadata, parsed_email, raw_message, debug=False):
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
        if await progress.attempt_send(clean_message,
                send_at=send_at,
                cli_obj=self,
                debug=debug):
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

        if progress.failed and changed:
            tag_failed = self.options['--tag-failed=']
            if tag_failed and in_index:
                await self.tag_message(metadata, tag_failed)

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

            if await self.process(md, parsed_email, raw_message,
                    debug=self.options['--debug'][-1]):
                processed.append(md)
            else:
                logging.debug('Not processed: %s' % md.idx)

        return processed
