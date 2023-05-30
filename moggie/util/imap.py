import imaplib
import logging
import re
import socket
import ssl
import traceback
import time
from imaplib import IMAP4, IMAP4_SSL

from ..api.exceptions import *
from ..email.metadata import Metadata
from .imap_utf7 import codecs
from .mailpile import PleaseUnlockError


# These are mailbox names we avoid downloading (by default)
BLACKLISTED_MAILBOXES = (
    'drafts',
    'chats',
    '[gmail]/all mail',
    '[gmail]/important',
    '[gmail]/starred',
    'openpgp_keys')


IMAP_TOKEN = re.compile(b'("[^"]*"'
                        b'|[\\(\\)]'
                        b'|[^\\(\\)"\\s]+'
                        b'|\\s+)')


def parse_imap(reply, decode=False):
    """
    This routine will parse common IMAP4 responses into Pythonic data
    structures.

    >>> parse_imap((b'OK', [b'1 (F (X Y) U {2}', b'12', 41]))
    (True, [b'1', [b'F', [b'X', b'Y'], b'U', b'12']])

    >>> parse_imap((b'OK', [b'1 (F (X Y) U {2}', b'12']))
    (True, [b'1', [b'F', [b'X', b'Y'], b'U', b'12']])

    >>> parse_imap((b'OK', [b'Two {10}', b'0123456789', b'Three']))
    (True, [b'Two', b'0123456789', b'Three'])

    >>> parse_imap((b'OK', [b'One (Two (Th ree)) "Four Five"']), decode=True)
    (True, ['One', ['Two', ['Th', 'ree']], 'Four Five'])

    >>> parse_imap((b'BAD', [b'Sorry']))
    (False, [b'Sorry'])
    """
    if not reply or len(reply) < 2:
        return False, []
    stack = []
    pdata = []
    if decode:
        _decode = lambda v: str(v, 'utf-8')
    else:
        _decode = lambda v: v
    next_is_blob = False
    try:
     for dline in reply[1]:
        while True:
            if next_is_blob:
                pdata.append(dline[:next_is_blob])
                dline = dline[next_is_blob:]
                next_is_blob = False

            if dline is None:
                m = ''
            elif isinstance(dline, bytes):
                m = IMAP_TOKEN.match(dline)
            else:
                print('WARNING: Unparsed IMAP response data: %s' % (dline,))
                m = None
            if m:
                token = m.group(0)
                dline = dline[len(token):]
                if token[:1] == b'"':
                    pdata.append(_decode(token[1:-1]))
                elif token[:1] == b'(':
                    stack.append(pdata)
                    pdata.append([])
                    pdata = pdata[-1]
                elif token[:1] == b')':
                    pdata = stack.pop(-1)
                elif token[:1] == b'{' and token[-1:] == b'}':
                    next_is_blob = int(token[1:-1])
                    break
                elif token[:1] not in (b'', b' ', b'\t', b'\n', b'\r'):
                    pdata.append(_decode(token))
            else:
                break
    except (IndexError, ValueError):
      logging.debug('Failed to parse: %s' % reply[1])
      raise
    while stack:
        pdata = stack.pop(-1)
    return (reply[0] in ('OK', 'ok', b'OK', b'ok')), pdata


def _imap_dict(parsed):
    d = {}
    while parsed:
        d[parsed[0]] = parsed[1]
        parsed = parsed[2:]
    return d


# Helper for use with _try_wrap
def _parsed_imap(func, *args, **kwargs):
    return parse_imap(func(*args, **kwargs))


class ConnectError(APIException):
    pass


class IMAPError(APIException):
    pass


def _try_wrap(conn, e_ctx, op, *args, **kwargs):
    _raising = None
    try:
        return op(*args, **kwargs)
    except APIException as exc:
        _raising = exc
    except (ssl.CertificateError, ssl.SSLError) as exc:
        _raising = ConnectError(
            'Failed to make a secure TLS connection: %s' % exc, **e_ctx)
        _raising.traceback = traceback.format_exc()
    except (IMAP4.error) as exc:
        err = str(exc)
        if '[AUTHENTICATIONFAILED]' in err:
            _raising = PleaseUnlockError(err)
        else:
            _raising = IMAPError(
                'An IMAP protocol error occurred: %s' % err, **e_ctx)
            _raising.traceback = traceback.format_exc()
    except (IOError, AttributeError, socket.error) as exc:
        _raising = IMAPError(
            'A network error occurred: %s' % exc, **e_ctx)
        _raising.traceback = traceback.format_exc()
    try:
        if conn:
            # Close the socket directly, in the hopes this will boot
            # any timed-out operations out of a hung state.
            conn.socket().shutdown(socket.SHUT_RDWR)
            conn.file.close()
    except (AttributeError, IOError, socket.error):
        pass
    if _raising is not None:
        logging.debug(str(_raising))
        raise _raising


def connect_imap(host,
        port=None, protocol='auto', conn_timeout=5, timeout=30, debug=0,
        conn_cls=None, conn=None):

    if ']:' in host and (host[:1] == '['):  # IPv6 [address]:port syntax
        host, port = host[1:].split(']:')
    elif ':' in host:
        host, port = host.rsplit(':', 1)

    # FIXME: Mailpile v1 had a very nice connection broker to make
    #        this stuff go over Tor and manage TLS certs in a fancy
    #        and user-visible way. Do we want to bring that back?

    if (protocol == 'auto') and conn is None:
        # A sensible defaults strategy; this should only suck if admins
        # deploy firewalls that make us hang for a long time.
        exc = None
        for _proto, _port in (('imaps', 993), ('imap+starttls', 143)):
            if (port is not None) and (_port != int(port)):
                continue
            try:
                return connect_imap(host,
                    port=_port, protocol=_proto,
                    conn_timeout=conn_timeout, timeout=timeout, debug=debug,
                    conn_cls=conn_cls, conn=conn)
            except APIException as e:
                exc = e
        if exc:
            raise exc
        else:
            # Auto on a weird port! Fall through to normal IMAP4 logic.
            protocol = 'imap'

    def _mkc(_cls, h, p):
        logging.debug('Connecting with %s to %s:%s' % (_cls.__name__, h, p))
        _conn = _cls(h, p, timeout=(conn_timeout or timeout))
        if hasattr(_conn, 'sock'):
            _conn.sock.settimeout(timeout)
            _conn.debug = debug
        return _conn

    def _capabilities(_conn):
        ok, data = _parsed_imap(_conn.capability)
        if ok:
            # We convert server-suppied values to upper-case.
            # Anything we add will be lower-case.
            return set(str(cap, 'utf-8').upper() for cap in data)
        else:
            return set()

    # Ports must be ints
    port = int(port or 143)

    # If we are given a conn class, use it. Allows mocks for testing.
    if not conn_cls:
        req_stls = (protocol in ('imap+starttls', 'starttls', 'imap_tls'))
        want_ssl = (protocol in ('imaps', 'imap_ssl'))
        conn_cls = IMAP4_SSL if want_ssl else IMAP4
    else:
        req_stls = want_ssl = False

    e_ctx = {
        'protocol': protocol, 'host': host, 'port': port,
        'conn_cls': conn.__class__.__name__ if conn else conn_cls.__name__}

    if conn is None:
        conn = _try_wrap(None, e_ctx, _mkc, conn_cls, host, port)
    caps = _try_wrap(conn, e_ctx, _capabilities, conn)

    if req_stls or ('STARTTLS' in caps and not want_ssl):
        ok, data = _try_wrap(conn, e_ctx, _parsed_imap, conn.starttls)
        if ok:
            caps = _try_wrap(conn, e_ctx, _capabilities, conn)
        if not ok:
            raise ConnectError(
                'Failed to secure the connection with TLS', **e_ctx)
        caps.add('has_tls')
    elif want_ssl:
        caps.add('has_tls')

    logging.debug('Connected with capabilities: %s / %s' % (caps, e_ctx))
    return conn, caps, e_ctx


class ImapConn:
    def __init__(self, username, host_port, password=None, debug=0):
        self.host_port = host_port
        self.username = username
        self.password = password
        self.authenticated = False
        self.conn = None
        self.conn_info = {}
        self.capabilities = set()
        self.selected = None
        self.host_port = host_port
        self.debug=debug
        if username and password:
            self.connect().unlock(username, password)

    def _id(self):
        if self.username:
            return 'imap://%s@%s' % (self.username.replace('@', '%40'), self.host_port)
        return 'imap://' + self.host_port

    def please_unlock(self, err=None):
        _id = self._id()
        err = err or 'Need username and password for %(id)s'
        self.shutdown()
        raise PleaseUnlockError(err % {'id': _id},
            resource=_id,
            username=(not self.username),
            password=True)

    def connect(self):
        if not self.conn:
            conn, caps, info = connect_imap(self.host_port, debug=self.debug)
            self.conn = conn
            self.capabilities = caps
            self.conn_info = info
        return self

    def unlock(self, username=None, password=None, ask_key=None, set_key=None):
        if self.authenticated:
            return self
        if password is None and self.password is None:
            self.please_unlock('Please login %(id)s')
        try:
            self.username = username or self.username or ''
            self.password = password or self.password or ''
            ok, data = _try_wrap(
                self.conn, self.conn_info, _parsed_imap,
                    self.connect().conn.login,
                    self.username, self.password or '')
            if ok:
                self.authenticated = True
                return self
        except PleaseUnlockError:
            self.please_unlock('Login incorrect for %(id)s')

    def _gather_responses(self, decode=True):
        # We convert server-suppied values to upper-case.
        # Our stuff is lower-case.
        responses = {}
        for attr in list(self.conn.untagged_responses.keys()):
            attr = attr.upper()
            if attr in ('OK',):  # This is imaplib garbage
                continue
            response = parse_imap(self.conn.response(attr), decode=decode)
            responses[attr] = (response[1] or [None])[0]
            if decode and attr in (
                    'EXISTS', 'RECENT', 'UNSEEN', 'UIDNEXT', 'UIDVALIDITY'):
                try:
                    responses[attr] = int(responses[attr])
                except ValueError:
                    pass
        return responses

    def select(self, mailbox, readonly=False):
        if isinstance(mailbox, str):
            mailbox = codecs.encode(mailbox, 'imap4-utf-7')
        if self.selected and self.selected.get('mailbox') == mailbox:
            return self

        ok, data = _try_wrap(
            self.conn, self.conn_info, _parsed_imap,
                self.unlock().conn.select, mailbox)
        if ok:
            self.selected = self._gather_responses()
            self.selected['mailbox'] = mailbox
            logging.debug('Selected: %s' % self.selected)
            return self
        raise KeyError('Failed to select %s' % mailbox)

    def uids(self, mailbox, skip=0):
        ok, data = _try_wrap(self.conn, self.conn_info,
            _parsed_imap, self.select(mailbox).conn.search, None, 'ALL')
        if ok:
            return [int(i) for i in data]
        return []

    def fetch_metadata(self, mailbox, uids):
        # Ask the server for only the headers we need for metadata; sharing
        # some of the parsing work and reducing network traffic.
        imap_headers = (
            'BODY.PEEK[HEADER.FIELDS %s]' % (Metadata.IMAP_HEADERS,))
        ok, data = _try_wrap(self.conn, self.conn_info,
            self.select(mailbox).conn.fetch,
                (','.join('%d' % i for i in uids)),
                '(RFC822.SIZE FLAGS %s)' % (imap_headers,))
        if not ok:
            return
        hkey = bytes(imap_headers.replace('.PEEK', ''), 'utf-8')
        for lines in data:
            if lines and lines[0] == 41:  # This is an imaplib bug
                continue
            try:
                # This is a hack to make response parsing less thorny
                if isinstance(lines, tuple):
                    lines = list(lines)
                    lines[0] = lines[0].replace(hkey, b'RFC822.HEADER')
                    lines = tuple(lines)

                _, (uid, data) = parse_imap(('OK', lines), decode=True)
                data = _imap_dict(data)
                if 'FLAGS' in data and 'RFC822.SIZE' in data:
                    yield (
                        int(uid),
                        int(data['RFC822.SIZE']),
                        data['FLAGS'],
                        data.get('RFC822.HEADER', b''))
                else:
                    raise ValueError('Flags or size not found')
            except (ValueError, IndexError) as e:
                logging.debug('Bogus data: %s, %s' % (lines, exc))

    def fetch_messages(self, mailbox, uids):
        ok, data = _try_wrap(self.conn, self.conn_info,
            self.select(mailbox).conn.fetch,
                (','.join('%d' % i for i in uids)),
                '(BODY[])')
        if not ok:
            return
        for lines in data:
            if lines and lines[0] == 41:  # This is an imaplib bug
                continue
            try:
                _, (uid, data) = parse_imap(('OK', lines), decode=True)
                data = _imap_dict(data)
                if 'BODY[]' in data:
                    yield (int(uid), data['BODY[]'])
                else:
                    raise ValueError('Message not found')
            except (ValueError, IndexError) as e:
                logging.debug('Bogus data: %s, %s' % (lines, exc))

    def close(self):
        if self.conn and self.selected:
            ok, data = _try_wrap(self.conn, self.conn_info,
                _parsed_imap, self.conn.close)
            self.selected = None
        return self

    def ls(self, prefix=None, recurse=False, limit=1000):
        if isinstance(prefix, str):
            prefix = codecs.encode(prefix, 'imap4-utf-7')
        if prefix in (None, '/', b'/'):
            prefix = b''

        folders = []
        ok, data = _try_wrap(
            self.conn, self.conn_info, _parsed_imap,
                self.unlock().conn.list, prefix or b'""', b'%')
        while ok:
            while len(data) >= 3:
                (flags, sep, path), data[:3] = data[:3], []
                flags = [str(f, 'utf-8').upper() for f in flags]
                try:
                    decoded_path = codecs.decode(path, 'imap4-utf-7')
                except UnicodeDecodeError:
                    decoded_path = ''
                yield (decoded_path, path, sep, flags)
                if limit is not None:
                    limit -= 1
                    if limit < 1:
                        return
                if recurse and '\\NOINFERIORS' not in flags:
                    folders.append((path + sep))
            if folders:
                ok, data = _try_wrap(
                    self.conn, self.conn_info, _parsed_imap,
                        self.conn.list, folders.pop(0), b'%')
            else:
                break

    def mailboxes(self, prefix=None, empty=False, limit=1000):
        for dp, path, sep, flags in self.ls(prefix, recurse=True, limit=None):
            if '\\NOSELECT' not in flags:
                yield (dp, path, sep, flags)
                if limit is not None:
                    limit -= 1
                    if limit < 1:
                        return

    def shutdown(self):
        if self.conn:
            try:
                self.close()
                self.conn.socket().close()
                self.conn.file.close()
            except (OSError, IOError):
                pass
            self.conn = None


##[ Test code follows ]#######################################################

if __name__ == "__main__":
    import doctest, json, sys

    args = sys.argv[1:]
    if 2 <= len(args) <= 3:
        logging.basicConfig(level=logging.DEBUG)
        try:
            import getpass
            ic = ImapConn(args[0], ':'.join(args[1:3]), debug=1)
            ic.unlock(None, getpass.getpass('Password: '))
            ic.select('INBOX')
            for info in ic.mailboxes('/'):
                print('%s' % (info,))
            for u,s,f,h in ic.fetch_metadata('INBOX', ic.uids('INBOX')):
                print('%d = %d bytes, %.40s' % (u, s, h))
            for uid, msg in ic.fetch_messages('INBOX', [1]):
                print('UID=%d\n%s' % (uid, str(msg, 'utf-8')))
            ic.close()
        except APIException as exc:
            sys.stderr.flush()
            print(json.dumps(exc.as_dict(), indent=2))
            print(exc.traceback)

    else:
        results = doctest.testmod(optionflags=doctest.ELLIPSIS)
        if results.failed:
            print('%s' % (results, ))
            sys.exit(1)
        else:
            print('Tests passed OK')
