# Utilities for sending mail
import base64
import logging
import time
import ssl

import aiosmtplib
import aiosmtplib.smtp
from aiosmtplib.response import SMTPResponse
from aiosmtplib.protocol import SMTPProtocol


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
    al = logging.getLogger('asyncio')
    al.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    al.addHandler(ch)

    # Monkey patch this, because they don't provide hooks. :-(
    aiosmtplib.smtp.SMTPProtocol = LoggingSMTPProtocol


class ServerAndSender:
    PROTO_SMTP_CLEARTEXT = 'smtpclr'
    PROTO_SMTP_BEST_EFFORT = 'smtp'  # Note: Does not verify certs
    PROTO_SMTP_STARTTLS = 'starttls'
    PROTO_SMTP_OVER_TLS = 'smtps'

    PROTOS = set(['smtp', 'smtpclr', 'smtptls', 'smtps'])

    PORT_SMTP = 25
    PORT_SMTPS = 465

    def __init__(self, via=None, sender=None, key=None):
        self._reset()

        self.sender = sender
        if key:
            self.parse_key(key)

        elif via:
            if via[:1] == '@':
                self.account = via[1:]
            elif via[:1] == '/':
                self.command = via
            elif via[:1] == '|':
                self.command = via[1:]
            else:
                self.parse_server_spec(via)

    use_tls = property(lambda s: (s.proto == s.PROTO_SMTP_OVER_TLS))

    use_starttls = property(lambda s: (s.proto in (
        s.PROTO_SMTP_BEST_EFFORT,
        s.PROTO_SMTP_STARTTLS)))

    validate_certs = property(lambda s: (s.proto in (
        s.PROTO_SMTP_OVER_TLS,
        s.PROTO_SMTP_STARTTLS)))

    def _reset(self):
        self.proto = self.PROTO_SMTP_BEST_EFFORT
        self.auth = None
        self.host = None
        self.port = None
        self.account = None
        self.command = None
        self.sender = None

    def __hash__(self):
        if self.account or self.command:
            return hash(str(self))
        return hash('%s/%s/%d/%s'
            % (self.proto, self.host, self.port, self.sender))

    def __str__(self):
        if self.account:
            return '@%s/%s' % (self.account, self.sender)
        elif self.command:
            return '|%s/%s' % (self.account, self.sender)
        return ('%s/%s/%s/%d/%s'
            % (self.proto, self.host, self.auth or '', self.port, self.sender))

    def parse_key(self, key):
        self._reset()

        if key[:1] == '@':
            self.account, self.sender = [
                k.strip() for k in key[1:].rsplit('/', 1)]
        elif key[:1] == '|':
            self.command, self.sender = [
                k.strip() for k in key[1:].rsplit('/', 1)]
        else:
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
            self.proto, sspec = sspec.rstrip('/').split('://')

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
            userpass, self.host = self.host.rsplit('@', 1)
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
    serializing/deserializing its state to/from Metadata annotations, and
    methods for attempting to send and update the progress state.
    """
    PENDING = 'p'
    REJECTED = 'r'  # Permanent errors
    DEFERRED = 'd'  # Temporary errors
    CANCELED = 'c'  # User cancelled
    SENT = 's'

    USE_MX = 'MX'

    FRIENDLY_STATUS = {
        PENDING: 'Ready',
        CANCELED: 'Cancelled',
        DEFERRED: 'Will retry',
        REJECTED: 'Rejected',
        SENT: 'Sent OK'}

    DEFERRED_BACKOFF = 30 * 60  # Wait at least 30 minutes after errors
    TIMEOUT = 5

    def __init__(self, metadata=None, annotations=None):
        self.status = {}
        self.history = []
        if isinstance(metadata, dict):
            self.from_annotations(metadata.get('annotations', {}))
        elif hasattr(metadata, 'annotations'):
            self.from_annotations(metadata.annotations)
        elif annotations is not None:
            self.from_annotations(annotations)

    all_recipients = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()])

    failed = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()
        if status[-1:] == s.REJECTED])

    unsent = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()
        if s.is_unsent(status)])

    done = property(lambda s: [
        rcpt for rcpt, status in s.get_rcpt_statuses()
        if not s.is_unsent(status)])

    next_send_time = property(lambda s: min([
            s._send_time(status) for rcpt, status in s.get_rcpt_statuses()
            if s.is_unsent(status)
        ]) if s.unsent else None)

    def is_unsent(self, status):
        return status[-1:] not in (self.SENT, self.CANCELED, self.REJECTED)

    def _send_time(self, status):
        send_ts = int(status[:-1], 16)
        if status[-1:] == self.DEFERRED:
            back_off_s = self.DEFERRED_BACKOFF * (1 + len(self.history))
            send_ts += back_off_s

        return send_ts

    def _is_ready(self, now, status):
        return (self._send_time(status) <= now)

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
            cli_obj=None,
            debug=False,
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
                if progress.is_unsent(s) and progress._is_ready(now, s)]
            if rcpts:
                try:
                    if cli_obj and ss.account:
                        worker = cli_obj.connect()
                        send_func = progress.api_send
                    elif ss.command:
                        send_func = progress.exec_send
                    else:
                        send_func = progress.smtp_send

                    if await send_func(sending_email, ss, rcpts,
                                timeout=timeout,
                                debug=debug):
                        made_changes = True
                except Exception as e:
                    logging.exception('Send -[%s]->%s failed' % (ss, rcpts))
                    progress.progress(progress.DEFERRED, ss, *rcpts,
                        log='Internal error: %s' % e)

        return made_changes

    def update_unsent_timestamps(progress, new_ts):
        # Iterate through the plan and add new progress events with the
        # requested timestamp.
        updates = []
        for ss, stats in progress.status.items():
            for rcpt, stat in stats.items():
                if progress.is_unsent(stat):
                    updates.append((progress.PENDING, ss, rcpt))
        for update in updates:
            progress.progress(*update, ts=new_ts)
        return bool(updates)

    def explain(progress):
        for ss, stats in progress.status.items():
            for rcpt, stat in sorted(stats.items()):
                if progress.is_unsent(stat):
                    statcode = stat[-1:]
                    ts = progress._send_time(stat)
                else:
                    ts, statcode = int(stat[:-1], 16), stat[-1:]
                yield (
                    ss.sender,
                    ss.host,
                    rcpt,
                    statcode,
                    progress.FRIENDLY_STATUS[statcode],
                    ts)

    async def smtp_send(progress, sending_email, ss, recipients,
            timeout=TIMEOUT, debug=False):
        enable_smtp_logging()

        try:
            if debug:
                asyncio.get_event_loop().set_debug(True)
            smtp_client = aiosmtplib.SMTP(
                hostname=ss.host,
                port=ss.port,
                use_tls=ss.use_tls,
                start_tls=ss.use_starttls,
                validate_certs=ss.validate_certs,
                timeout=timeout)
        except:
            if debug:
                asyncio.get_event_loop().set_debug(False)
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

        except aiosmtplib.errors.SMTPRecipientsRefused as e:
            logging.debug('Recipients refused: %s' % e)
            progress.progress(progress.REJECTED, ss, *recipients, log=e)

        except aiosmtplib.errors.SMTPException as e:
            logging.debug('SMTPException: %s' % e)
            progress.progress(progress.DEFERRED, ss, *recipients, log=e)

        except (IOError, OSError, ssl.SSLCertVerificationError) as e:
            progress.progress(progress.DEFERRED, ss, *recipients, log=e)

        finally:
            if debug:
                asyncio.get_event_loop().set_debug(False)

        try:
            smtp_client.close()
        except (IOError, OSError):
            pass

        return True


##############################################################################

STATUS_CONNECTING = 'connecting'
STATUS_CONNECT_FAILED = 'connect_failed'
STATUS_LOGIN_OK = 'login_ok'
STATUS_LOGIN_REJECTED = 'login_rejected'
STATUS_FROM_OK = 'from_ok'
STATUS_FROM_REJECTED = 'from_rejected'
STATUS_RCPT_OK = 'rcpt_ok'
STATUS_RCPT_REJECTED = 'rcpt_rejected'
STATUS_MESSAGE_SEND_PROGRESS = 'sending'
STATUS_MESSAGE_SEND_OK = 'send_ok'
STATUS_MESSAGE_SEND_FAILED = 'send_failed'
STATUS_DONE = 'done'


def _progress(callback, good, status, details, message=''):
    message = message % details
    result = (callback is None) or callback(good, status, details, message)
    if result and message:
        if good:
            logging.info(message)
        else:
            logging.warning(message)
            logging.debug(to_json(details))
    return good


def _update(base, **updates):
    stuff = copy.copy(base)
    stuff.update(updates)
    return stuff


def parse_partial_url(url,
        default_proto='smtp',
        default_username=None,
        default_password=None,
        default_port=25,
        default_path=None):
    """
    Parse a proto://user:password@host:port/path string into components,
    using the defaults provided if components are left unspecified.

    Returns: (proto, username, password, hostname, port, path)
    """
    try:
        proto, url = url.split('://')
    except ValueError:
        proto = default_proto

    try:
        userpass, url = url.split('@', 1)
        try:
            username, password = userpass.split(':')
        except ValueError:
            username = userpass
            password = default_password
    except ValueError:
        username = default_username
        password = default_password

    try:
        url, path = url.split('/', 1)
    except ValueError:
        path = default_path

    try:
        hostname, port = url.split(':')
        port = int(port)
    except ValueError:
        hostname = url
        port = default_port

    return proto, username, password, hostname, port, path


async def sendmail(message_bytes, via_from_rcpt_tuples,
        progress_callback=None):
    """
    This method will iterate through the (via, from, recipients)
    tuples and attempt to send the message to each. Progress is logged
    and optionally reported back using the given callback.

    The callback should have the following signature:

       def sendmail_progress_callback(good, status, details, message):
           ...

    Status will be one of the constants:

        STATUS_CONNECTING
        STATUS_CONNECT_FAILED
        STATUS_LOGIN_OK
        STATUS_LOGIN_FAILED
        STATUS_FROM_OK
        STATUS_FROM_REJECTED
        STATUS_RCPT_OK
        STATUS_RCPT_REJECTED
        STATUS_MESSAGE_SEND_PROGRESS
        STATUS_MESSAGE_SEND_OK
        STATUS_MESSAGE_SEND_FAILED
        STATUS_DONE

    The good variable is just a boolean, False for errors and true otherwise.
    The details will be a dictionary of extra attributes explaining further.
    The message is human readable text.

    Callbacks can return False to suppress logging.
    """
    global SENDMAIL_HANDLERS
    happy = True
    for i, (via, frm, recipients) in enumerate(via_from_rcpt_tuples):
        tried = False
        try:
            for prio, test, handler in sorted(SENDMAIL_HANDLERS):
                if test(via):
                    if not await handler(
                            message_bytes, via, frm, recipients,
                            i, progress_callback):
                        happy = False
                    tried = True
                    break
            if not tried:
                raise ValueError('Cannot send via %s' % via)
        except Exception as e:
            import traceback
            details = {
                'error': str(e),
                'handler': str(handler),
                'traceback': traceback.format_exc()}
            happy = _progress(progress_callback,
                False, STATUS_DONE, details, 'Error sending: %(error)s')

    _progress(progress_callback, happy, STATUS_DONE, {})
    return happy


def _safe_str(data):
    try:
        return str(data, 'utf-8')
    except UnicodeDecodeError:
        import base64
        return 'base64:' + str(base64.b64encode(data), 'utf-8')


async def sendmail_exec(message_bytes, via, frm, recipients, _id, progress_cb):
    if via[:1] == '|':
        via = via[1:].strip()
    args = {
        'from': frm,
        'to_list': '__TO_LIST__',
        'to': ','.join(recipients)}
    command = [word % args for word in via.split()]
    if '__TO_LIST__' in command:
        i = command.index('__TO_LIST__')
        command = command[:i] + [str(r) for r in recipients] + command[i+1:]
    details = {
        'id': _id,
        'via': via,
        'from': frm,
        'recipients': recipients}

    happy = True
    import threading
    from .safe_popen import Safe_Popen, PIPE
    try:
        _progress(progress_cb, True, STATUS_CONNECTING,
            _update(details, command=command),
            'Running: %(command)s')
        proc = Safe_Popen(command, stdin=PIPE, stdout=PIPE, stderr=PIPE)

        _progress(progress_cb,
            True, STATUS_MESSAGE_SEND_PROGRESS, details, 'Sending message')

        details2 = {}
        details2.update(details)

        def _collect(what, src):
            details2[what] = _safe_str(src.read())
        c1 = threading.Thread(target=_collect, args=('stdout', proc.stdout))
        c2 = threading.Thread(target=_collect, args=('stderr', proc.stderr))
        c1.daemon = True
        c2.daemon = True
        c1.start()
        c2.start()

        proc.stdin.write(message_bytes)
        proc.stdin.close()
        details2['sent_bytes'] = len(message_bytes)
        details2['exit_code'] = ec = proc.wait()
        c1.join()
        c2.join()

        if ec == 0:
            happy = _progress(progress_cb,
                True, STATUS_MESSAGE_SEND_OK, details2,
                'Message sent OK (%(sent_bytes)s bytes)')
        else:
            happy = _progress(progress_cb,
                False, STATUS_MESSAGE_SEND_FAILED, details2,
                'Sending failed, exit code=%(exit_code)s')

    except Exception as e:
        happy = _progress(progress_cb,
            False, STATUS_MESSAGE_SEND_FAILED,
            _update(details, error=str(e)),
            'Sending failed, error=%(error)s')

    return happy


async def sendmail_smtp(message_bytes, via, frm, recipients, _id, progress_cb,
        partial_send=False,
        timeout=120):

    details = {
        'id': _id,
        'via': via,
        'from': frm,
        'recipients': recipients}

    proto, user, pwd, host, port, _ = parse_partial_url(via,
        default_proto='auto',
        default_port=25)
    if proto == 'auto':
        if port == 465:
            proto = 'smtps'
        else:
            proto = 'smtp'

    require_starttls = (proto == 'smtptls')
    if proto == 'smtptls':
        proto = 'smtp'

    # FIXME: Bring back SMTorP!
    # FIXME: Port over and use Mailpile v1's connection broker?

    import aiosmtplib
    happy = True
    server = exc_error = exc_msg = None
    try:
        _progress(progress_cb,
            True, STATUS_CONNECTING,
            _update(details,
                 proto=proto,
                 username=user,
                 password='(password)' if pwd else None,
                 host=host,
                 port=port),
            'Connecting to: ' + (
            '%(proto)s://%(username)s:%(password)s@%(host)s:%(port)d'
            if (user or pwd) else '%(proto)s://%(host)s:%(port)d'))


        async def _server_connect():
            exc_error, exc_msg = (
                STATUS_CONNECT_FAILED,
                'Failed to connect to server: %(error)s')
            server = aiosmtplib.SMTP(
                hostname=host,
                port=port,
                start_tls=False,  # We handle this below
                use_tls=(proto == 'smtps'),
                local_hostname='mailpile.local',
                timeout=timeout,
                validate_certs=False,  # FIXME: Poor crypto better than none?
                client_cert=None,      # FIXME
                client_key=None)       # FIXME
            await server.connect()
            if server.is_ehlo_or_helo_needed:
                await server.ehlo()
            return server

        server = await _server_connect()

        # We always try to enable TLS, even if the user only requested
        # plain-text SMTP. But we only throw errors if the user asked
        # for encryption.
        try:
            await server.starttls()
            if server.is_ehlo_or_helo_needed:
                await server.ehlo()
        except Exception as e:
            if require_starttls:
                exc_msg = 'STARTTLS failed, could not encrypt: %(error)s'
                raise
            else:
                server = await _server_connect()

        if user or pwd:
            exc_error, exc_msg = (
                STATUS_LOGIN_REJECTED, 'Login failed: %(error)s')
            await server.login(user or '', pwd or '')
            _progress(progress_cb, True, STATUS_LOGIN_OK, details,
                'Logged in to server as %s' % user)

        exc_error, exc_msg = (
            STATUS_FROM_REJECTED,
            'Sender (%(from)s) rejected by server: %(error)s')
        await server.mail(frm)
        _progress(progress_cb, True, STATUS_FROM_OK, details,
            'Server accepted sender')

        exc_error, exc_msg = (
            STATUS_RCPT_REJECTED,
            'Recipient (%(rcpt)s) rejected by server: %(error)s')
        for rcpt in recipients:
            try:
                await server.rcpt(rcpt)
            except Exception as e:
                happy = _progress(progress_cb,
                    False, exc_error,
                    _update(details, rcpt=rcpt, error=str(e)),
                    exc_msg)
        if happy:
            _progress(progress_cb, True, STATUS_RCPT_OK, details,
                'Server accepted all recipients')
        elif not partial_send:
            return False

        exc_error, exc_msg = (
            STATUS_MESSAGE_SEND_FAILED,
            'Failed to upload message to server: %(error)s')
        await server.data(message_bytes)
        _progress(progress_cb, True, STATUS_MESSAGE_SEND_OK,
            _update(details, sent_bytes=len(message_bytes)),
            'Message sent (%(sent_bytes)d bytes)')

    except Exception as e:
        happy = _progress(progress_cb,
            False, exc_error or STATUS_MESSAGE_SEND_FAILED,
            _update(details, error=str(e)),
            exc_msg or 'Sending failed, error=%(error)s')
    finally:
        if server:
            server.close()

    return happy


# This sets the stage for some sort of plugin adding other
# protocols for sending mail, by adding entries with prorities
# below 999.
SENDMAIL_HANDLERS = [
    (500,  lambda via: (via[:1] in ('|', '/')),  sendmail_exec),
    (999,  lambda via: True,                     sendmail_smtp)]
