# Utilities for sending mail
import copy
import logging

from .dumbcode import to_json, from_json


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


if __name__ == '__main__':
    def _assert(val, want=True, msg='assert'):
        if isinstance(want, bool):
            if (not val) == (not want):
                want = val
        if val != want:
            raise AssertionError('%s(%s==%s)' % (msg, val, want))

    _assert(
        parse_partial_url('http://user:pass@host:443/path/to/stuff'),
        ('http', 'user', 'pass', 'host', 443, 'path/to/stuff'))
    _assert(
        parse_partial_url('user@host/path/to/stuff'),
        ('smtp', 'user', None, 'host', 25, 'path/to/stuff'))
    _assert(
        parse_partial_url('localhost:125'),
        ('smtp', None, None, 'localhost', 125, None))
    _assert(
        parse_partial_url('user:secret@localhost:125'),
        ('smtp', 'user', 'secret', 'localhost', 125, None))

    print('Tests pass OK')
