import asyncio
import logging
import os
import re
import sys
import socket
import time
import threading
import traceback

from upagekite import uPageKite, uPageKiteConnPool, LocalHTTPKite
from upagekite.httpd import HTTPD, url, async_url
from upagekite.proto import uPageKiteDefaults
from upagekite.web import process_post, http_require

from ..config import APPNAME as MAIN_APPNAME
from ..config import APPURL as MAIN_APPURL
from ..config import AppConfig
from ..util.dumbcode import to_json

from .base import BaseWorker


class WorkerPageKiteSettings(uPageKiteDefaults):
    APPNAME = MAIN_APPNAME
    APPURL = MAIN_APPURL
    APPVER = '2.0.0'

    MAX_POST_BYTES = 256*1024*1024

    PARSE_HTTP_HEADERS = re.compile(
        '^(Auth'
        '|Accept'
        '|Con[nt]'
        '|Cook'
        '|Host'
        '|Orig'
        '|Sec-Web'
        '|Upgrade'
        '|User-Agent)[^:]*:')

    info = logging.info
    error = logging.error
    debug = logging.debug
    trace = logging.debug

    @classmethod
    async def network_send_sleep(uPK, sent):
      pass


class MoggieConnPool(uPageKiteConnPool):
    # FIXME: This should get pushed upstream to upagekite, it's not at all
    #        moggie specific; any platform with real threads wants this.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loop = None
        self._queue = None
        self._poller = None

    def event_queue(self):
        if self._queue is None:
            _id = '%x' % int(time.time())
            self._loop = asyncio.get_event_loop()
            self._queue = asyncio.Queue()
            self._poller = threading.Thread(target=self._poll_t, args=(_id,))
            self._poller.daemon = True
            self._poller.start()
        return self._queue

    def _poll_t(self, _id):
        timeout = 100
        while self._queue:
            # Hanging forever will prevent us from picking up new sockets.
            # FIXME: This might still need more work.
            polled = self.poll.poll(timeout)
            if polled and self._queue:
                if self.pk.uPK.trace:
                    self.pk.uPK.trace('[%s] poll() returned: %s (timeout=%d)'
                        % (_id, polled, timeout))
                asyncio.run_coroutine_threadsafe(
                    self._queue.put(polled), self._loop)
                timeout = 50
                time.sleep(0.01)
            else:
                timeout = min(timeout + 100, 60000)

    def close(self):
        self._queue = None

    async def async_poll(self, timeout_ms):
        queue = self.event_queue()
        results = [[]]
        async def getter():
            results.append(await queue.get())
        try:
            await asyncio.wait_for(getter(), timeout=(timeout_ms / 1000.0))
        except:
            pass
        return results[-1]


class MoggiePageKiteManager(uPageKite):
    def get_conn_pool(self, conns):
        return MoggieConnPool(conns, self)


class RequestTimer:
    def __init__(self, name, req_env, status=None):
        self.t0 = time.time()
        self.status = status
        self.name = name
        self.stats = req_env['worker'].status

    def __enter__(self, *args, **kwargs):
        return self

    def __exit__(self, *args, **kwargs):
        if self.status is not None:
            k = self.name +'_'+ self.status
            self.stats[k] = self.stats.get(k, 0) + 1
        else:
            k = self.name
        k += '_ms'
        t = 1000 * (time.time() - self.t0)
        self.stats[k] = (0.95*self.stats.get(k, t)) + (0.05*t)


@url('/ping', '/ping/*')
@http_require(methods=('POST',), csrf=False, secure_transport=False)
def web_ping(req_env):
    return {
        'ttl': 30,
        'msg': 'PONG',
        'mimetype': 'text/plain; charset="UTF-8"',
        'body': 'Pong'}


@url('/quit', '/quit/*')
@http_require(methods=('POST',), csrf=False, local=True)
def web_quit(req_env):
    req_env['postpone_action'](lambda: req_env['worker'].quit())
    return {
        'ttl': 30,
        'mimetype': 'application/json',
        'body': '{"quitting": true}'}


@url('/status', '/status/*')
@http_require(methods=('POST',), csrf=False, local=True)
def web_status(req_env):
    return {
        'ttl': 30,
        'mimetype': 'application/json',
        'body': to_json(req_env['worker'].status, indent=1)}


@async_url('/rpc/*')
@http_require(methods=('POST',), csrf=False, local=True)
@process_post(max_bytes=WorkerPageKiteSettings.MAX_POST_BYTES, _async=True)
async def web_rpc(req_env):
    return await req_env['worker'].handle_web_rpc(req_env)


class WorkerHTTPD(HTTPD):
    CODE_STATUS = {
        200: 'ok',
        403: 'denied',
        400: 'ignored',
        404: 'ignored',
        500: 'failed'}

    def __init__(self, worker, public_paths, public_prefixes, *args, **kwargs):
        HTTPD.__init__(self, *args, **kwargs)
        self._public_paths = public_paths or []
        self._public_prefixes = public_prefixes or []
        self._worker = worker
        self.status = self._worker.status

    def log_request(self, *args, **kwargs):
        scode = 'requests_%s' % self.CODE_STATUS.get(args[3], args[3])
        self.status[scode] = self.status.get(scode, 0) + 1
        super().log_request(*args, **kwargs)

    def get_handler(self, path, headers):
        public = (path in self._public_paths)
        for prefix in self._public_prefixes:
            if path.startswith(prefix):
                public = True
                break

        secret = ''
        logging.debug('headers=%s' % headers)
        if headers.get('Cookie', '').startswith('moggie_token='):
            secret = headers['Cookie'].split('=', 1)[-1]
            if path.startswith('/' + secret):
                path = path[len(secret)+1:]
        if not public and (not secret or path[:2] == '/@'):
            try:
                _, secret, path = path.split('/', 2)
            except ValueError:
                if not secret:
                    raise PermissionError('Missing secret')
        path = '/' + path.lstrip('/')

        (func, fa) = HTTPD.get_handler(self, path, headers)
        while (not func) and ('/' in path):
            path = path.rsplit('/', 1)[0]
            (func, fa) = HTTPD.get_handler(self, path+'/*', headers)
        if func is None:
            return None, fa

        if secret[:1] == '@':
            secret = secret[1:]
        access = self._worker._check_access(bytes(secret, 'utf-8'), path)
        if (func is not None) and (not public) and (not access):
            raise PermissionError('Bad secret')

        def mk_func_wrap(a):
            def func_wrap(req_env, *args, **kwargs):
                req_env['access'] = a
                return func(req_env, *args, **kwargs)
            return func_wrap

        return mk_func_wrap(access), fa


class PublicWorker(BaseWorker):
    KIND = 'public'
    STATIC_PATH = '.'
    PUBLIC_PATHS = []
    PUBLIC_PREFIXES = []
    CONFIG_SECTION = None

    def __init__(self, profile_dir,
            host=None, port=None, kite_name=None, kite_secret=None, name=None,
            log_level=logging.ERROR):

        self.profile_dir = profile_dir
        self.worker_dir = os.path.join(profile_dir, 'workers')
        if not os.path.exists(self.worker_dir):
            os.mkdir(self.worker_dir, 0o700)

        BaseWorker.__init__(self, self.worker_dir,
            host=host, port=port, name=name, log_level=log_level)

        self.httpd = None
        self.kite = None
        self.pk_manager = None

        self.kite_name = kite_name or (self.KIND + '.local' )
        self.kite_secret = kite_secret

        self.app = self.get_app()
        self.shared_req_env = {'app': self.app, 'worker': self}

        self._rpc_response = None
        self._rpc_response_map = {
            self.HTTP_400: {'code': 400, 'msg': 'Invalid Request'},
            self.HTTP_403: {'code': 403, 'msg': 'Access Denied'},
            self.HTTP_404: {'code': 404, 'msg': 'Not Found'},
            self.HTTP_500: {'code': 500, 'msg': 'Internal Error'}}

    @classmethod
    def FromArgs(cls, workdir, args):
        host = 'localhost'
        port = '0'
        kite_name = kite_secret = None
        cfg = AppConfig(workdir)

        log_level = cfg.get(cfg.GENERAL, 'log_level', fallback=logging.ERROR)

        if cls.CONFIG_SECTION:
            port = cfg.get(cls.CONFIG_SECTION, 'port', fallback=port)
            port = cfg.get(cls.CONFIG_SECTION, 'listen', fallback=port)
            kite_name = cfg.get(cls.CONFIG_SECTION, 'kite_name', fallback=kite_name)
            kite_secret = cfg.get(cls.CONFIG_SECTION, 'kite_secret', fallback=kite_secret)

        if len(args) >= 1:
            port = args.pop(0)
        if ':' in port:
            host, port = port.split(':')

        if len(args) >= 2:
            kite_name = args.pop(0)
            kite_secret = args.pop(0)

        return cls(workdir,
            host=host, port=int(port),
            kite_name=kite_name, kite_secret=kite_secret,
            log_level=int(log_level))

    def get_app(self):
        return None

    def quit(self):
        if hasattr(self, 'pk_manager') and self.pk_manager:
            self.pk_manager.keep_running = False
        else:
            return super().quit()

    def _ping(self, timeout=None):
        pong = b'HTTP/1.0 200 PONG'
        if not timeout:
            timeout = 60 if self._is_public() else 1
        try:
            host_hdr = 'Host: %s\r\n' % self.kite_name
            conn = self._conn('ping', timeout=timeout, headers=host_hdr)
            conn.shutdown(socket.SHUT_WR)
            result = conn.recv(len(pong))
        except Exception as e:
            logging.debug('PING failed: %s' % e)
            result = None
        if result and (result != pong):
            logging.debug('Unexpected PING response: %s' % result)
        return (result == pong)

    def startup_tasks(self):
        pass

    def shutdown_tasks(self):
        pass

    def client_info_tuple(self):
        return [None, None]

    def reply(self, what, *args, **kwargs):
        _rpc_response = self._rpc_response_map.get(what)
        if not _rpc_response:
            raise Exception('Not Implemented')
        kwargs['client_info_tuple'][1] = _rpc_response

    def reply_json(self, data, **kwargs):
        kwargs['client_info_tuple'][1] = {
            'ttl': 30,
            'mimetype': 'application/json',
            'body': to_json(data, indent=1) + '\n'}

    async def handle_web_rpc(self, req_env):
        args = bytes(req_env.request_path, 'latin-1').split(b'/')[3:]
        func = b'rpc/' + args.pop(0)
        c_i_t = self.client_info_tuple()
        await self.async_rpc_handler(
            func,
            req_env.http_method,
            args,
            req_env.query_tuples,
            lambda m: {'method': m},
            lambda: req_env.payload,
            client_info_tuple=c_i_t)
        return c_i_t[1]

    def _is_public(self):
        return (self.kite_name and self.kite_secret and True)

    def _main_httpd_loop(self):
        self.startup_tasks()
        try:
            uPK = WorkerPageKiteSettings
            port = self._sock.getsockname()[1]

            self.httpd = WorkerHTTPD(self,
                self.PUBLIC_PATHS, self.PUBLIC_PREFIXES,
                self.kite_name, self.STATIC_PATH, self.shared_req_env, uPK)

            self.kite = LocalHTTPKite(self._sock,
                self.kite_name, self.kite_secret,
                handler=self.httpd.handle_http_request)
            self.kite.listening_port = port

            self.pk_manager = MoggiePageKiteManager([self.kite],
                socks=[self.kite],
                public=self._is_public(),
                uPK=uPK)

            self.pk_manager.run()
        except KeyboardInterrupt:
            pass
        except:
            logging.exception('_main_httpd_loop loop crashed')
        finally:
            self.shutdown_tasks()


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.DEBUG)
    aw = PublicWorker.FromArgs('/tmp', sys.argv[1:])
    if aw.connect():
        try:
            print('** We are live, yay')
            #print(aw.capabilities())
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
