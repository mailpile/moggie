import os
import sys
import socket

from upagekite import uPageKite, LocalHTTPKite
from upagekite.httpd import HTTPD, url
from upagekite.proto import uPageKiteDefaults

from ..config import APPNAME as MAIN_APPNAME
from ..config import APPURL as MAIN_APPURL
from ..config import AppConfig

from .base import BaseWorker


def require(req_env, post=True, local=False, secure=True):
    if post and not (req_env.http_method == 'POST'):
        raise PermissionError('Unsupported method')
    if (local or secure) and not (
            req_env.remote_ip.startswith('127.') or
            req_env.remote_ip.startswith('::ffff:127.') or
            req_env.remote_ip == '::1'):
        if local:
            raise PermissionError('Method is localhost-only, got %s' % req_env.remote_ip)
        if secure and not req_env.frame.tls:
            raise PermissionError('Method requires TLS or localhost')


@url('/ping', '/ping/*')
def web_ping(req_env):
    require(req_env, post=True, secure=False)
    return {
        'ttl': 30,
        'msg': 'PONG',
        'mimetype': 'text/plain; charset="UTF-8"',
        'body': 'Pong'}


@url('/quit')
def web_quit(req_env):
    require(req_env, post=True, local=True)
    req_env['postpone_action'](lambda: sys.exit(0))
    return {
        'ttl': 30,
        'mimetype': 'application/json',
        'body': '{"quitting": true}'}


class WorkerPageKiteSettings(uPageKiteDefaults):
    APPNAME = MAIN_APPNAME
    APPURL = MAIN_APPURL
    APPVER = '2.0.0'

    info = uPageKiteDefaults.log
    error = uPageKiteDefaults.log
    debug = uPageKiteDefaults.log
    trace = uPageKiteDefaults.log


class WorkerHTTPD(HTTPD):
    def __init__(self, secret, public_paths, public_prefixes, *args, **kwargs):
        HTTPD.__init__(self, *args, **kwargs)
        self._secret = str(secret, 'latin-1')
        self._public_paths = public_paths or []
        self._public_prefixes = public_prefixes or []

    def get_handler(self, path, headers):
        public = (path in self._public_paths)
        for prefix in self._public_prefixes:
            if path.startswith(prefix):
                public = True
                break

        if not public:
            try:
                _, secret, path = path.split('/', 2)
            except ValueError:
                raise PermissionError('Missing secret')
        path = '/' + path.lstrip('/')

        (func, fa) = HTTPD.get_handler(self, path, headers)
        while (not func) and ('/' in path):
            path = path.rsplit('/', 1)[0]
            (func, fa) = HTTPD.get_handler(self, path+'/*', headers)

        if (func is not None) and (not public) and (secret != self._secret):
            # FIXME: Allow more nuanced access control, some secrets will
            #        be valid for some things but not others.
            raise PermissionError('Bad secret')

        return func, fa


class PublicWorker(BaseWorker):
    KIND = 'public'
    STATIC_PATH = '.'
    PUBLIC_PATHS = []
    PUBLIC_PREFIXES = []
    CONFIG_SECTION = None

    def __init__(self, profile_dir,
            host=None, port=None, kite_name=None, kite_secret=None, name=None):

        self.profile_dir = profile_dir
        self.worker_dir = os.path.join(profile_dir, 'workers')
        if not os.path.exists(self.worker_dir):
            os.mkdir(self.worker_dir, 0o700)

        BaseWorker.__init__(self, self.worker_dir, host=host, port=port, name=name)

        self.httpd = None
        self.kite = None
        self.pk_manager = None

        # FIXME: Override these!
        self.kite_name = kite_name or (self.KIND + '.local' )
        self.kite_secret = kite_secret

        self.shared_req_env = {'app': self}

    @classmethod
    def FromArgs(cls, workdir, args):
        port = 0
        kite_name = kite_secret = None

        if cls.CONFIG_SECTION:
            cfg = AppConfig(workdir)
            port = int(cfg.get(cls.CONFIG_SECTION, 'port', fallback=port))
            kite_name = cfg.get(cls.CONFIG_SECTION, 'kite_name', fallback=kite_name)
            kite_secret = cfg.get(cls.CONFIG_SECTION, 'kite_secret', fallback=kite_secret)

        if len(args) >= 1:
            port = int(args.pop(0))
        if len(args) >= 2:
            kite_name = args.pop(0)
            kite_secret = args.pop(0)

        return cls(workdir,
            port=port, kite_name=kite_name, kite_secret=kite_secret)

    def get_config(self):
        return AppConfig(self.profile_dir)

    def quit(self):
        return self.call('quit')

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

    def startup_tasks(self):
        pass

    def _main_httpd_loop(self):
        self.startup_tasks()

        uPK = WorkerPageKiteSettings
        port = self._sock.getsockname()[1]

        self.httpd = WorkerHTTPD(self._secret,
            self.PUBLIC_PATHS, self.PUBLIC_PREFIXES,
            self.kite_name, self.STATIC_PATH, self.shared_req_env, uPK)

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
    import sys
    aw = PublicWorker.FromArgs('/tmp', sys.argv[1:])
    if aw.connect():
        try:
            print('** We are live, yay')
            #print(aw.capabilities())
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
