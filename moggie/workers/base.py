import json
import os
import socket
import sys
import time
import traceback

try:
    from setproctitle import getproctitle, setproctitle
except ImportError:
    setproctitle = None

from base64 import b64encode
from multiprocessing import Process

from ..config import APPNAME
from ..util.dumbcode import *


class QuitException(Exception):
    pass


class BaseWorker(Process):
    """
    An extremely simple authenticated HTTP/1.0 RPC server.
    """
    KIND = "base"

    ACCEPT_TIMEOUT = 5
    LISTEN_QUEUE = 50
    LOCALHOST = 'localhost'
    PEEK_BYTES = 4096
    REQUEST_OVERHEAD = 128  # A conservative estimate

    HTTP_200 = b'HTTP/1.0 200 OK\r\n'
    HTTP_400 = b'HTTP/1.0 400 Invalid Request\r\nContent-Length: 16\r\n\r\nInvalid Request\n'
    HTTP_403 = b'HTTP/1.0 403 Access Denied\r\nX-MP: Sorry\r\nContent-Length: 14\r\n\r\nAccess Denied\n'
    HTTP_404 = b'HTTP/1.0 404 Not Found\r\nContent-Length: 10\r\n\r\nNot Found\n'
    HTTP_500 = b'HTTP/1.0 500 Internal Error\r\nContent-Length: 15\r\n\r\nInternal Error\n'

    HTTP_JSON = HTTP_200 + b'Content-Type: application/json\r\n'
    HTTP_OK   = HTTP_JSON + b'Content-Length: 17\r\n\r\n{"result": true}\n'

    def __init__(self, status_dir, name=KIND):
        Process.__init__(self)

        self.name = name
        self.keep_running = True
        self.url = None
        self.status = {
            'pid': os.getpid(),
            'started': int(time.time()),
            'requests_ok': 0,
            'requests_ignored': 0,
            'requests_failed': 0}
        self.functions = {
            b'quit':   (b'', self.api_quit),
            b'status': (None, self.api_status)}

        self._secret = b64encode(os.urandom(18), b'-_').strip()
        self._status_file = os.path.join(status_dir, name + '.url')
        self._sock = None
        self._client = None
        self._client_addrinfo = None
        self._client_peeked = None
        self._client_headers = None


    def run(self):
        if setproctitle:
            setproctitle('%s: %s' % (APPNAME, self.KIND))
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.bind((self.LOCALHOST, 0))
            self._sock.settimeout(self.ACCEPT_TIMEOUT)
            self._sock.listen(self.LISTEN_QUEUE)

            (s_host, s_port) = self._sock.getsockname()
            self.url = self._make_url(s_host, s_port)
            with open(self._status_file, 'w') as fd:
                fd.flush()
                os.chmod(self._status_file, 0o600)
                fd.write(self.url)

            return self._main_httpd_loop()
        except KeyboardInterrupt:
            pass
        finally:
            os.remove(self._status_file)

    def _make_url(self, s_host, s_port):
        return 'http://%s:%d/%s' % (s_host, s_port, str(self._secret, 'utf-8'))

    def _main_httpd_loop(self):
        while self.keep_running:
            client = None
            try:
                (client, c_addrinfo) = self._sock.accept()
                peeked = client.recv(self.PEEK_BYTES, socket.MSG_PEEK)
                if ((peeked[:4] in (b'GET ', b'PUT ', b'POST', b'HEAD'))
                        and (b'\r\n\r\n' in peeked)):
                    try:
                        method, path = peeked.split(b' ', 2)[:2]
                        secret, args = path.split(b'/', 2)[1:3]
                    except ValueError:
                        secret = b''
                    if secret == self._secret:
                        self._client, client = client, None
                        self._client_addrinfo = c_addrinfo
                        self._client_peeked = peeked
                        self._client_method = method
                        self._client_args = args
                        self._client_headers = None
                        self.handler(str(method, 'latin-1'), args)
                    else:
                        self.status['requests_ignored'] += 1
                        client.send(secret and self.HTTP_403 or self.HTTP_400)
                else:
                    self.status['requests_ignored'] += 1
                    client.send(self.HTTP_400)
            except OSError:
                pass
            except QuitException:
                self.keep_running = False
            except:
                traceback.print_exc()
                self.status['requests_failed'] += 1
                if client:
                    client.send(self.HTTP_500)
            finally:
                if client:
                    client.close()

    def api_quit(self):
        self.keep_running = False
        self.reply_json({'quitting': True})

    def api_status(self, *args):
        if args and args[0] == 'as.text':
            lines = ['%s: %s' % (k, self.status[k]) for k in self.status]
            self.reply(
                self.HTTP_200 + b'Content-Type: text/plain\r\n',
                ('\n'.join(sorted(lines))).encode('utf-8') + b'\n')
        else:
            self.reply_json(self.status)

    def _load_url(self):
        try:
            with open(self._status_file, 'r') as fd:
                self.url = fd.read().strip()
        except:
            self.url = None
        return self.url

    def _conn(self, path, method='GET', timeout=60, headers='', secret=None):
        try:
            proto, _, hostport, url_secret = self.url.split('/', 3)
            fmt = '%s /%s/%s HTTP/1.0\r\n%s\r\n'
        except ValueError:
            proto, _, hostport = self.url.split('/', 2)
            url_secret = secret = ''
            fmt = '%s /%s%s HTTP/1.0\r\n%s\r\n'

        host, port = hostport.split(':')

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(max(1, timeout//30))
        sock.connect((host, int(port)))
        sock.settimeout(timeout)

        sock.send((fmt % (method, secret or url_secret, path, headers)
            ).encode('latin-1'))

        return sock

    def _ping(self):
        try:
            conn = self._conn('ping', timeout=1, secret='-')
            conn.shutdown(socket.SHUT_WR)
            result = conn.recv(len(self.HTTP_403))
        except:
            result = None
        return (result == self.HTTP_403)

    def connect(self, autostart=True):
        if (self.url or self._load_url()) and self._ping():
            return self

        if autostart:
            try:
                os.remove(self._status_file)
            except FileNotFoundError:
                pass

            self.url = None
            self.start()
            for t in range(1, 11):
                if not self._load_url():
                    time.sleep(0.05 * t)

            if self.url and self._ping():
                return self

        return None

    def call(self, fn, *args, qs=None, method='GET', upload=None):
        # This will raise a KeyError if the function isn't defined
        fn = fn.encode('latin-1') if isinstance(fn, str) else fn
        argsig, func = self.functions[fn]
        fn = str(fn, 'latin-1')

        # Format positional arguments and query string
        path = fn
        if args:
            path += ('/' + '/'.join([dumb_encode_asc(a) for a in args]))
        if qs:
            path += ('?' + '&'.join(
                '%s=%s' % (k, dumb_encode_asc(qs[k])) for k in qs))
        if len(path) > (self.PEEK_BYTES - self.REQUEST_OVERHEAD):
            if upload is None:
                # Support arbitrarily large arguments, via POST
                upload = path.encode('latin-1')
                path = fn + '/*'
            else:
                raise ValueError('Too many arguments')

        if upload:
            conn = self._conn(path,
                method='POST',
                headers='Content-Length: %d\r\n' % len(upload))
            for i in range(0, len(upload)//4096 + 1):
                conn.send(upload[i*4096:(i+1)*4096])
            conn.shutdown(socket.SHUT_WR)
        else:
            conn = self._conn(path, method=method)
            if method in ('GET', 'HEAD'):
                conn.shutdown(socket.SHUT_WR)

        peeked = conn.recv(self.PEEK_BYTES, socket.MSG_PEEK)
        if peeked.startswith(self.HTTP_200):
            hdr = peeked.split(b'\r\n\r\n', 1)[0]
            junk = conn.recv(len(hdr) + 4)
            conn = conn.makefile(mode='rb')
            if b'application/json' in hdr:
                return json.load(conn)
            else:
                return (hdr, conn)
        else:
            # FIXME: Parse the HTTP response code and raise better exceptions
            raise PermissionError(str(peeked[:12], 'latin-1'))

    def reply(self, pre, data=b'', close=True):
        if data:
            pre += b'Content-Length: %d\r\n\r\n' % len(data)
            self._client.send(pre + data)
            data_len = b'%d' % (len(pre) + len(data))
        else:
            self._client.send(pre)
            data_len = b'%d' % len(pre)
        if close:
            self._client.close()
        else:
            data_len = b'..'

        # FIXME: This is not a good way to do logging
        print(str(
            b'%s - %s %s - %s /%s' % (
                self._client_addrinfo[0].encode('latin-1'),
                pre[9:12],
                data_len,
                self._client_method,
                self._client_args), 'latin-1'))

    def start_sending_data(self, mimetype, length):
        self.reply(self.HTTP_200
            + (b'Content-Length: %d\r\n' % (length))
            + (b'Content-Type: %s\r\n\r\n' % mimetype.encode('utf-8')),
            close=False)
        return self._client

    def reply_json(self, data):
        self.reply(self.HTTP_JSON,
            json.dumps(data, indent=1).encode('utf-8') + b'\n')

    def parse_header(self, hdr):
        hdr_lines = str(hdr, 'latin-1').replace('\r', '').splitlines()
        return dict([ln.split(': ') for ln in hdr_lines[1:]])

    def request_headers(self):
        if not self._client_headers:
            hdr = self._client_peeked.split(b'\r\n\r\n', 1)[0]
            self._client_headers = self.parse_header(hdr)
        return self._client_headers

    def get_upload_size_and_fd(self):
        return (
            int(self.request_headers().get('Content-Length', 0)),
            self._client.makefile('rb'))

    def get_uploaded_data(self):
        ln, fd = self.get_upload_size_and_fd()
        return fd.read(ln)

    def decode_args(self, args):
        return [dumb_decode(a) for a in args]

    def handler(self, method, args):
        t0 = time.time()
        a_and_q = args.split(b'?', 1)
        args = a_and_q[0].split(b'/')

        fn = args.pop(0)
        argsig_and_func = self.functions.get(fn)
        fn = str(fn, 'latin-1')

        if argsig_and_func is not None:
            try:
                argsig, func = argsig_and_func

                kwargs = {}
                if len(a_and_q) > 1:
                    pairs = [p.split(b'=', 1) for p in a_and_q[1].split(b'&')]
                    kwargs = dict(
                        (str(p[0], 'latin-1'), dumb_decode(p[1]))
                        for p in pairs)
                if method == 'POST':
                    hdr = self._client_peeked.split(b'\r\n\r\n')[0]
                    self._client.recv(len(hdr) + 4)
                    kwargs['method'] = method
                else:
                    self._client.recv(len(self._client_peeked))

                # Support arbitrarily large arguments, via POST
                if method == 'POST' and (len(args) == 1) and (args[0] == b'*'):
                    posted = self.get_uploaded_data()
                    a_and_q = posted.split(b'?', 1)
                    args = a_and_q[0].split(b'/')[1:]
                    del kwargs['method']

                if argsig is not None:
                    if len(argsig) != len(args):
                        return self.reply(self.HTTP_400)
                    for i, p in enumerate(argsig):
                        if (args[i][0] != p or not args[i]) and (p not in b'*'):
                            return self.reply(self.HTTP_400)

                    rv = func(*[dumb_decode(a) for a in args], **kwargs)
                else:
                    rv = func(*args, **kwargs)

                t = time.time() - t0
                stats = self.status
                stats[fn+'_ok'] = stats.get(fn+'_ok', 0) + 1
                stats[fn+'_t'] = 0.95*stats.get(fn+'_t', t) + 0.05*t
                stats['requests_ok'] += 1
                return rv
            except TypeError:
                traceback.print_exc()
                if kwargs:
                    self.status['requests_ignored'] += 1
                    return self.reply(self.HTTP_400)  # This is a guess :-(
            except:
                traceback.print_exc()
            self.status['requests_failed'] += 1
            self.reply(self.HTTP_500)
        else:
            self.status['requests_ignored'] += 1
            self.reply(self.HTTP_404)


if __name__ == '__main__':

    class TestWorker(BaseWorker):
        def __init__(self, *args, **kwargs):
            BaseWorker.__init__(self, *args, **kwargs)
            self.functions.update({
                b'ping': (None, self.api_ping)})

        def api_ping(self, *args, pong='PONG', method='GET'):
            args = self.decode_args(args)
            if method != 'GET':
                try:
                    uploaded = self.get_uploaded_data()
                    print('** Received upload: %s' % uploaded)
                except:
                    traceback.print_exc()
            self.reply_json({pong: args})

    tw = TestWorker('/tmp', name='moggie-test-worker').connect()
    if tw:
        try:
            r = tw.call('ping', None, '\0\1\2', 1.976, [1,2],
                upload=b'12345',
                qs={'pong': 'oh'})
            print('** Got: %s' % r)
            assert(r['oh'][0] is None)
            assert(r['oh'][1] == '\0\1\2')

            try:
                tw.call('ping', *[a for a in range(0, 10000)])
                tw.call('ping', *[a for a in range(0, 10000)], upload=b'0')
                assert(not 'reached')
            except ValueError:
                pass

            print('** Tests passed, waiting... **')
            tw.join()
        finally:
            tw.terminate()
