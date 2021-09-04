import socket
import traceback

from upagekite import uPageKite, LocalHTTPKite
from upagekite.httpd import HTTPD, url
from upagekite.proto import uPageKiteDefaults

from ..config.paths import APPNAME as MAIN_APPNAME
from .base import BaseWorker


@url('/ping', '/ping/*')
def web_ping(req_env):
    return {
        'ttl': 30,
        'code': 200,
        'msg': 'PONG',
        'mimetype': 'text/plain; charset="UTF-8"',
        'body': 'Pong'}


@url('/in/*')
def web_in(req_env):
    return {
        'ttl': 30,
        'code': 500,
        'msg': 'Unimplemented',
        'mimetype': 'text/plain; charset="UTF-8"',
        'body': 'FIXME'}


class AppPageKiteSettings(uPageKiteDefaults):
    APPNAME = MAIN_APPNAME
    APPURL = 'https://github.com/mailpile/'
    APPVER = '2.0.0'

    info = uPageKiteDefaults.log
    error = uPageKiteDefaults.log
    debug = uPageKiteDefaults.log
    #trace = uPageKiteDefaults.log


class AppHTTPD(HTTPD):
    def get_handler(self, path):
        (func, fa) = HTTPD.get_handler(self, path)
        while (not func) and ('/' in path):
            path = path.rsplit('/', 1)[0]
            (func, fa) = HTTPD.get_handler(self, path+'/*')
        return func, fa


class AppWorker(BaseWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'app'

    def __init__(self, profile_dir, name=KIND):
        BaseWorker.__init__(self, profile_dir, name=name)

        self.httpd = None
        self.kite = None
        self.pk_manager = None

        # FIXME: Override these!
        self.kite_name = 'mailpile.local'
        self.kite_secret = None

        self.shared_req_env = {'app_worker': self}

    def _ping(self):
        pong = b'HTTP/1.0 200 PONG'
        try:
            host_hdr = 'Host: %s\r\n' % self.kite_name
            conn = self._conn('ping', timeout=1, headers=host_hdr)
            conn.shutdown(socket.SHUT_WR)
            result = conn.recv(len(pong))
        except:
            result = None
        return (result == pong)

    def _make_url(self, s_host, s_port):
        return 'http://%s:%d' % (s_host, s_port)

    def _main_httpd_loop(self):
        uPK = AppPageKiteSettings
        port = self._sock.getsockname()[1]

        self.httpd = AppHTTPD(self.kite_name, '.', self.shared_req_env, uPK)

        self.kite = LocalHTTPKite(self._sock,
            self.kite_name, self.kite_secret,
            handler=self.httpd.handle_http_request)
        self.kite.listening_port = port

        self.pk_manager = uPageKite([self.kite],
            socks=[self.kite],
            public=(self.kite_name and self.kite_secret),
            uPK=uPK)

        self.pk_manager.run()


if __name__ == '__main__':
    aw = AppWorker('/tmp').connect()
    if aw:
        try:
            print('** We are live, yay')
            #print(aw.capabilities())
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
