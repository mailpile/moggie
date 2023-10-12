import asyncio
import base64
import copy
import inspect
import logging
import os
import socket
import time
import threading
import traceback

try:
    import signal
except ImportError:
    signal = None

try:
    from setproctitle import getproctitle, setproctitle
except ImportError:
    setproctitle = None

from multiprocessing import Process

from ..api.exceptions import *
from ..config import APPNAME, AppConfig, configure_logging
from ..util.dumbcode import *
from ..util.fds import close_private_fds
from ..util.http import url_parts, http1x_connect


def _qsp(qs_raw):
    return [p.split(b'=', 1) for p in qs_raw.split(b'&')]


class QuitException(Exception):
    pass


class BaseWorker(Process):
    """
    An extremely simple authenticated HTTP/1.0 RPC server.
    """
    KIND = "base"
    NICE = 0  # Raise this number to lower worker priority
    LOG_STDOUT = False

    # By default we disallow GET and HEAD, because these are RPC
    # services, not public facing and certainly not intended for
    # indexing (accidental or otherwise) by search engines.
    METHODS = (b'PUT ', b'POST')  # Note: must be 4 bytes.

    ACCEPT_TIMEOUT = 5
    LISTEN_QUEUE = 50
    LOCALHOST = 'localhost'
    PEEK_BYTES = 4096
    READ_BYTES = 1024 * 64
    REQUEST_OVERHEAD = 128  # A conservative estimate

    BACKGROUND_TASK_SLEEP = 0.1

    # Intervals for on_tick() and on_idle() events. Neither are precise.
    IDLE_T = 60
    TICK_T = 300
    SHUTDOWN_IDLE = False

    HTTP_200 = b'HTTP/1.0 200 OK\r\n'
    HTTP_400 = b'HTTP/1.0 400 Invalid Request\r\nContent-Length: 16\r\n\r\nInvalid Request\n'
    HTTP_403 = b'HTTP/1.0 403 Access Denied\r\nX-MP: Sorry\r\nContent-Length: 14\r\n\r\nAccess Denied\n'
    HTTP_404 = b'HTTP/1.0 404 Not Found\r\nContent-Length: 10\r\n\r\nNot Found\n'
    HTTP_424 = b'HTTP/1.0 424 Failed Dependency\r\n'
    HTTP_500 = b'HTTP/1.0 500 Internal Error\r\nContent-Length: 15\r\n\r\nInternal Error\n'

    HTTP_JSON = HTTP_200 + b'Content-Type: application/json\r\n'
    HTTP_OK   = HTTP_JSON + b'Content-Length: 17\r\n\r\n{"result": true}\n'

    def __init__(self, status_dir,
            host=None, port=None, name=None, notify=None,
            log_level=logging.ERROR, shutdown_idle=None):
        Process.__init__(self)

        self.name = name or self.KIND
        self.keep_running = True
        self.url = None
        self.url_parts = None
        self.status = {
            'pid': os.getpid(),
            'started': int(time.time()),
            'requests_ok': 0,
            'requests_ignored': 0,
            'requests_failed': 0}
        self.functions = {
            b'quit':      (True,  self.api_quit),
            b'noop':      (True,  self.api_noop),
            b'functions': (True,  self.api_functions),
            b'exception': (True,  self.api_exception),
            b'status':    (False, self.api_status)}

        # Support mutt-style log levels, convert to Pythonish
        if 0 <= log_level <= 4:
            log_level = [
                logging.CRITICAL,
                logging.ERROR,
                logging.WARNING,
                logging.INFO,
                logging.DEBUG
                ][log_level]

        self.shutdown_idle = (
            self.SHUTDOWN_IDLE if (shutdown_idle is None) else shutdown_idle)

        self.log_level = log_level
        self._notify = notify
        self._secret = base64.b64encode(os.urandom(18), b'-_').strip()
        self._auth_header = ''
        self._status_file = os.path.join(status_dir, self.name + '.url')
        self._want_host = host or self.LOCALHOST
        self._want_port = port or 0
        self._sock = None
        self._caller = None
        self._caller_lock = threading.Lock()
        self._client = None
        self._client_args = None
        self._client_addrinfo = None
        self._client_peeked = None
        self._client_method = None
        self._client_access = None
        self._client_headers = None
        self._background_jobs = {'default': []}
        self._background_threads = {}
        self._background_job_lock = threading.Lock()
        # FIXME: Check for stale url files; we just started up, so if one
        #        exists and we cannot connect, nuke it!

    def expose_object(self, obj,
            prefix='', allow_local=True, arg_filter=None, exclude=[]):
        for attr in dir(obj):
            method = getattr(obj, attr)
            if (attr[:1] == '_') or 'method' not in str(type(method)):
                continue
            if attr in exclude:
                continue

            def mk_wrappers(name, meth):
                def dict_wrap(encode, *args, **kwargs):
                    result = None
                    try:
                        if arg_filter is not None:
                            args = arg_filter(args)
                            kwargs = arg_filter(kwargs)
                        result = meth(*args, **kwargs)
                        return {
                            'method': name,
                            'result': result}
                    except KeyboardInterrupt:
                        raise
                    except Exception as e:
                        logging.exception('%s(...) => %s' % (name, result))
                        return {
                            'method': name,
                            'error': str(e),
                            'details': traceback.format_exc()}

                def api_wrap(*args, method=None, **kwargs):
                    return self.reply_json(dict_wrap(True, *args, **kwargs))

                def wrap(*args, **kwargs):
                    if self.url or not allow_local:
                        result = self.call(prefix + name, *args, qs=kwargs)
                    else:
                        result = dict_wrap(False, *args, **kwargs)
                    return result

                async def async_wrap(*args, **kwargs):
                    if self.url or not allow_local:
                        loop = asyncio.get_event_loop()
                        result = await self.async_call(
                            loop, prefix + name, *args, **kwargs)
                    else:
                        result = dict_wrap(False, *args, **kwargs)
                    return result

                return async_wrap, wrap, api_wrap

            async_wrap, wrap, api_wrap = mk_wrappers(attr, method)
            self.functions[bytes(prefix + attr, 'utf-8')] = (True, api_wrap)
            setattr(self, prefix + attr, wrap)
            setattr(self, prefix + 'async_' + attr, async_wrap)

    def log_more(self, *ignored_args):
        if self.log_level <= logging.DEBUG:
            return
        self.log_level -= 10
        configure_logging(
            type(self).__name__, stdout=self.LOG_STDOUT, level=self.log_level)
        logging.log(self.log_level,
            'Lowered log threshold to %d' % self.log_level)

    def run(self):
        if signal is not None:
            signal.signal(signal.SIGUSR2, self.log_more)
        configure_logging(
            type(self).__name__, stdout=self.LOG_STDOUT, level=self.log_level)
        logging.info('Started %s(%s), pid=%d'
            % (type(self).__name__, self.name, os.getpid()))

        close_private_fds()
        try:
            if self.NICE and hasattr(os, 'nice'):
                os.nice(self.NICE)
            if setproctitle:
                setproctitle('%s: %s' % (APPNAME, self.name))

            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.bind((self._want_host, self._want_port))
            self._sock.settimeout(self.ACCEPT_TIMEOUT)
            self._sock.listen(self.LISTEN_QUEUE)

            (s_host, s_port) = self._sock.getsockname()
            self.url = self._make_url(s_host, s_port)
            self.url_parts = url_parts(self.url)[1:]
            with open(self._status_file, 'w') as fd:
                fd.flush()
                os.chmod(self._status_file, 0o600)
                fd.write(self.url)

            return self._main_httpd_loop()
        except KeyboardInterrupt:
            pass
        except:
            logging.exception('Crashed!')
        finally:
            self._sock.close()
            logging.info('Stopped %s(%s), pid=%d'
                % (type(self).__name__, self.name, os.getpid()))
            try:
                os.remove(self._status_file)
                logging.shutdown()
            except FileNotFoundError:
                pass

    def _make_url(self, s_host, s_port):
        return 'http://%s:%d/%s' % (s_host, s_port, str(self._secret, 'utf-8'))

    def on_idle(self, last_active):
        if self.shutdown_idle:
            if last_active < (time.time() - self.shutdown_idle):
                self.quit()

    def on_tick(self):
        pass

    def _check_access(self, secret, args):
        return (secret == self._secret)

    def _main_httpd_loop(self):
        last_active = time.time()
        next_tick = int(last_active + self.TICK_T)
        self._sock.settimeout(self.IDLE_T)
        while self.keep_running:
            client = None
            now = int(time.time())
            if now >= next_tick:
                next_tick += (1 + (now-next_tick) // self.TICK_T) * self.TICK_T
                self.on_tick()
            try:
                (client, c_addrinfo) = self._sock.accept()
                last_active = time.time()
                peeked = client.recv(self.PEEK_BYTES, socket.MSG_PEEK)
                if ((peeked[:4] in self.METHODS) and (b'\r\n\r\n' in peeked)):
                    try:
                        method, path = peeked.split(b' ', 2)[:2]
                        secret, args = path.split(b'/', 2)[1:3]
                    except ValueError:
                        secret, args = b'', None
                    access = self._check_access(secret, args)
                    if access:
                        self._client, client = client, None
                        self._client_addrinfo = c_addrinfo
                        self._client_peeked = peeked
                        self._client_method = method
                        self._client_access = access
                        self._client_args = args
                        self._client_headers = None
                        self.handler(str(method, 'latin-1'), args)
                    else:
                        logging.debug(
                            'Invalid secret (for %s): %s' % (args, secret))
                        self.status['requests_ignored'] += 1
                        client.send(secret and self.HTTP_403 or self.HTTP_400)
                else:
                    logging.warning('Bad method or data: %s' % peeked[:20])
                    self.status['requests_ignored'] += 1
                    client.send(self.HTTP_400)
            except socket.timeout:
                self.on_idle(last_active)
            except OSError:
                pass
            except (QuitException, KeyboardInterrupt):
                self.keep_running = False
            except:
                logging.exception('Error in main HTTP loop')
                self.status['requests_failed'] += 1
                if client:
                    client.send(self.HTTP_500)
            finally:
                if client:
                    client.close()

    def quit(self):
        self.keep_running = False
        if self._sock is None:
            return self.call('quit')

    def api_quit(self, **kwargs):
        self.keep_running = False
        # FIXME: Wait for background jobs or abort them? Abort?
        self.reply_json({'quitting': True})

    def api_noop(self, *args, **kwargs):
        self.reply_json({'noop': True})

    def api_functions(self, *args, **kwargs):
        self.reply_json([str(fn, 'utf-8') for fn in self.functions])

    def api_exception(self, *args, **data):
        raise APIException(*args, **data)

    def api_status(self, *args, **kwargs):
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
            self.url_parts = url_parts(self.url)[1:]
        except:
            self.url_parts = self.url = None
        return self.url

    def _conn(self, path,
            method='POST', timeout=60, headers='', more=False, secret=None,
            prep_only=False):
        host, port, url_secret = self.url_parts
        if secret is not None:
            url_secret = '/' + secret
        if url_secret[-1:] != '/':
            url_secret += '/'
        return http1x_connect(host, port, url_secret + path,
            method=method, timeout=timeout, more=more, headers=headers,
            prep_only=prep_only)

    def _ping(self, timeout=1):
        conn = None
        try:
            conn = self._conn('ping', timeout=timeout, secret='-')
            result = conn.recv(len(self.HTTP_403))
        except Exception as e:
            logging.debug('PING failed (%s)' % e)
            result = None
        if conn:
            conn.close()
        if (result != self.HTTP_403):
            logging.debug('Unexpected PING response: %s' % result)
        return (result == self.HTTP_403)

    def connect(self, autostart=True, quick=False):
        if (self.url or self._load_url()) and (quick or self._ping()):
            return self

        if autostart:
            try:
                os.remove(self._status_file)
            except FileNotFoundError:
                pass

            self.url = self.url_parts = None
            if self.exitcode is not None:
                logging.debug('Cannot relaunch %s(%s)'
                    % (type(self).__name__, self.name,))
                return None

            logging.debug('Launching %s(%s)'
                % (type(self).__name__, self.name,))

            # Run in a separate thread, to avoid asyncio clashes
            th = threading.Thread(target=self.start)
            th.start()
            th.join()

            for t in range(1, 25):
                if self._load_url() and self._ping():
                    break
                logging.debug('Still not responding, waiting ...')
                time.sleep(0.05 * t)

            if self.url and self._ping():
                return self

        return None

    def notify(self, message, data=None, caller=None):
        logging.info('Notify%s%s: %s'
            % (' ' if caller else '', caller or '', message))
        if self._notify:
            try:
                notification = {'message': message}
                if data is not None:
                    notification['data'] = data
                if caller is not None:
                    notification['caller'] = caller
                self.call(self._notify, notification)
            except KeyboardInterrupt:
                raise
            except PermissionError:
                logging.error('Failed to notify %s' % self._notify)
            except:
                logging.exception('Failed to notify %s' % self._notify)

    def callback_url(self, fn):
        return '%s/%s' % (self.url, fn)

    def results_to_callback_chain(self, callback_chain, rv, tries=3):
        """
        This is an ad-hoc pattern for chaining operations together; the first
        argument is data to process, the second a chain of URLs to pass the
        results to. Each function in the chain is expected to take those two
        arguments, perform calculations and pass to the first URL in the chain,
        passing the rest of the chain as a second argument.

        Combining this with the singleton background job queue gives us
        multi-CPU parallel processing pipelines, yay!
        """
        if not callback_chain:
            return

        callback_url = callback_chain.pop(0)
        for tries in range(0, tries):
            try:
                self.call(callback_url, rv, callback_chain or None)
                return
            except PermissionError:
                logging.exception('Failed to call %s' % callback_url)
            except OSError:
                logging.exception('Failed to call %s' % callback_url)
                time.sleep(2**tries)

    def _background_worker(self, which, queue):
        while True:
            try:
                with self._background_job_lock:
                    job = queue.pop(0)
                job()
            except:
                logging.exception('Background job failed')
            if self.BACKGROUND_TASK_SLEEP:
                time.sleep(self.BACKGROUND_TASK_SLEEP)
            with self._background_job_lock:
                if not queue:
                    del self._background_threads[which]
                    logging.debug('Background worker finished, exiting.')
                    return

    def _start_background_workers(self):
        with self._background_job_lock:
            for which, queue in self._background_jobs.items():
                bgw = self._background_threads.get(which)
                if queue and not (bgw and bgw.is_alive()):
                    bgw = threading.Thread(
                        target=self._background_worker,
                        args=(which, queue))
                    self._background_threads[which] = bgw
                    bgw.daemon = True  # FIXME: Is this sane?
                    bgw.start()

    def add_background_job(self, job, first=False, which='default'):
        with self._background_job_lock:
            queue = self._background_jobs.get(which)
            if queue is None:
                queue = self._background_jobs[which] = []

            if first:
                queue[:0] = [job]
            else:
                queue.append(job)
        self._start_background_workers()

    def set_rpc_authorization(self, auth_header=None):
        if auth_header:
            self._auth_header = 'Authorization: %s\r\n' % auth_header
        else:
            self._auth_header = ''

    def with_caller(self, caller):
        if caller:
            self._caller_lock.acquire()
            self._caller = caller
        return self

    def get_caller(self):
        if self._caller:
            try:
                return self._caller
            finally:
                self._caller = None
                self._caller_lock.release()
        return None

    def _call_return(self, hdr, data):
        if b'application/json' in hdr:
            if data:
                data = from_json(data)
                if data and 'exception' in data:
                    reraise(data)
            return data
        else:
            return (hdr, data)

    async def async_call(self, loop, fn, *args,
            qs=None, method='POST', upload=None, data_cb=None, hide_qs=False):

        upload, (conn, conn_args, on_connect) = self.call(fn, *args,
            qs=qs, method=method, upload=upload, hide_qs=hide_qs,
            prep_only=True)

        # Actually make the connection: this is likely to block if the
        # worker is busy or the server far away.
        await loop.sock_connect(conn, conn_args)
        # This sends a small amount of data, but is quite unlikely to
        # block, so we don't bother making it async.
        on_connect()

        if upload:
            logging.debug(
                'async_call(%s), uploading %d bytes'  % (fn, len(upload)))
            try:
                conn.settimeout(len(upload) // 10)
                await loop.sock_sendall(conn, upload)
                conn.shutdown(socket.SHUT_WR)
            except BrokenPipeError as e:
                logging.warning('Upload(%s) failed: %s' % (path, e))
                conn.close()
                raise
        else:
            logging.debug('async_call(%s)'  % (fn,))

        try:
            conn.settimeout(None)
            conn.setblocking(False)
            peeked = await loop.sock_recv(conn, self.PEEK_BYTES)
        except socket.timeout:
            logging.warning('TIMED OUT: %s' % (fn,))
            conn.close()
            raise
        except:
            logging.exception('Error receiving from socket')
            conn.close()
            raise

        if (peeked.startswith(self.HTTP_200)
               or peeked.startswith(self.HTTP_424)):
            hdr, data = peeked.split(b'\r\n\r\n', 1)
            if data_cb is not None:
                data_cb(hdr, data)
                while True:
                    chunk = await loop.sock_recv(conn, self.READ_BYTES)
                    if not chunk:
                        break
                    data_cb(None, chunk)
                conn.close()
            else:
                while True:
                    chunk = await loop.sock_recv(conn, self.READ_BYTES)
                    if not chunk:
                        break
                    data += chunk
                conn.close()
                return self._call_return(hdr, data)
        else:
            conn.close()
            # FIXME: Parse the HTTP response code and raise better exceptions
            raise PermissionError(str(peeked[:12], 'latin-1'))

    # FIXME: We really would like this to be available as async, so
    #        we can multiplex things while our workers work.
    def call(self, fn, *args,
            qs=None, method='POST', upload=None, prep_only=False,
            hide_qs=False):
        fn = fn.encode('latin-1') if isinstance(fn, str) else fn
        remote = fn[:6] in (b'http:/', b'https:')
        if remote:
            parts = list(url_parts(str(fn, 'latin-1'))[1:])
            path = parts[-1].strip('/')
            caller = None
        else:
            # This will raise a KeyError if the function isn't defined
            argdecode, func = self.functions[fn]
            fn = str(fn, 'latin-1')
            path = fn
            caller = self.get_caller()

        # Format positional arguments and query string
        args = [caller] + list(args)
        if args:
            path += ('/' + '/'.join([dumb_encode_asc(a) for a in args]))
        if qs:
            path += ('?' + '&'.join(
                '%s=%s' % (k, dumb_encode_asc(qs[k])) for k in qs))
        if hide_qs or len(path) > (self.PEEK_BYTES - self.REQUEST_OVERHEAD):
            if upload is None:
                # Support arbitrarily large arguments, via POST
                upload = path.encode('latin-1')
                path = fn + '/*'
            else:
                raise ValueError('Too many arguments')

        if remote:
            parts[-1] = path
            conn_method = lambda **kw: http1x_connect(*parts, **kw)
        else:
            conn_method = lambda **kw: self._conn(path, **kw)

        if upload:
            conn = conn_method(
                method='POST',
                headers=(self._auth_header
                    + 'Content-Length: %d\r\n' % len(upload)),
                more=True,
                prep_only=prep_only)
            if prep_only:
                return upload, conn
            try:
                for i in range(0, len(upload), 4096):
                    conn.send(upload[i:i+4096])
                conn.shutdown(socket.SHUT_WR)
            except BrokenPipeError as e:
                logging.warning('Upload(%s) failed: %s' % (path, e))
                raise
        else:
            conn = conn_method(method=method, headers=self._auth_header,
                prep_only=prep_only)
        if prep_only:
            return upload, conn

        try:
            peeked = conn.recv(self.PEEK_BYTES, socket.MSG_PEEK)
        except socket.timeout:
            logging.warning('TIMED OUT: %s' % (path,))
            if conn:
                conn.close()
            raise

        if (peeked.startswith(self.HTTP_200)
                or peeked.startswith(self.HTTP_424)):
            hdr = peeked.split(b'\r\n\r\n', 1)[0]
            junk = conn.recv(len(hdr) + 4)
            conn = conn.makefile(mode='rb')
            if b'application/json' in hdr:
                result = self._call_return(hdr, conn.read())
                conn.close()
                return result
            else:
                return (hdr, conn)
        else:
            # FIXME: Parse the HTTP response code and raise better exceptions
            if conn:
                conn.close()
            raise PermissionError(str(peeked[:12], 'latin-1'))

    def client_info_tuple(self):
        return [
            None,
            self._client,
            self._client_addrinfo,
            self._client_args,
            self._client_method]

    def reply(self, pre, data=b'', close=True, client_info_tuple=None):
        if client_info_tuple is None:
            client_info_tuple = self.client_info_tuple()

        caller, client, cli_ai, cli_args, cli_method = client_info_tuple
        if data:
            pre += b'Content-Length: %d\r\n\r\n' % len(data)
            client.send(pre + data)
            data_len = b'%d' % (len(pre) + len(data))
        else:
            client.send(pre)
            data_len = b'%d' % len(pre)
        if close:
            client.close()
        else:
            data_len = b'..'

        # FIXME: This is not a good way to do logging
        logging.info(str(
            b'%s %s %s %s - %s /%s' % (
                cli_ai[0].encode('latin-1'),
                bytes(caller or '-', 'utf-8'),
                pre[9:12],
                data_len,
                cli_method,
                cli_args), 'latin-1'))

    def start_sending_data(self, mimetype, length):
        self.reply(self.HTTP_200
            + (b'Content-Length: %d\r\n' % (length))
            + (b'Content-Type: %s\r\n\r\n' % mimetype.encode('utf-8')),
            close=False)
        return self._client

    def reply_json(self, data, client_info_tuple=None, http_code=None):
        if isinstance(data, dict):
            if client_info_tuple and client_info_tuple[0]:
                data['_caller'] = client_info_tuple[0]
            elif self._caller:
                data['_caller'] = self._caller
        http_code = self.HTTP_200 if (http_code is None) else http_code
        self.reply(http_code + self.HTTP_JSON,
            to_json(data).encode('utf-8') + b'\n',
            client_info_tuple=client_info_tuple)

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
        a_and_q = args.split(b'?', 1)
        args = a_and_q[0].split(b'/')
        fn = args.pop(0)

        def prep(method):
            kwargs = {}
            if method == 'POST':
                hdr = self._client_peeked.split(b'\r\n\r\n')[0]
                self._client.recv(len(hdr) + 4)
                kwargs['method'] = method
            else:
                self._client.recv(len(self._client_peeked))
            return kwargs

        qs_pairs = _qsp(a_and_q[1]) if (len(a_and_q) > 1) else []
        return self.common_rpc_handler(fn,
             method, args, qs_pairs,
             prep,
             self.get_uploaded_data)

    # FIXME: This duplicates the code below almost completely, it would
    #        be nice to refactor and avoid that...
    async def async_rpc_handler(self,
            fn, method, args, qs_pairs, prep, uploaded,
            reply=None, client_info_tuple=None):
        t0 = time.time()
        argdecode_and_func = self.functions.get(fn)
        fn = str(fn, 'latin-1')

        if client_info_tuple is None:
            client_info_tuple = self.client_info_tuple()
        def async_reply(msg):
            return self.reply(msg, client_info_tuple=client_info_tuple)
        def async_reply_json(data, http_code=None):
            return self.reply_json(data,
                client_info_tuple=client_info_tuple, http_code=http_code)

        kwargs = None
        if argdecode_and_func is not None:
            try:
                argdecode, func = argdecode_and_func
                kwargs = prep(method)

                # Support arbitrarily large arguments, via POST
                if method == 'POST' and (len(args) == 1) and (args[0] == b'*'):
                    posted = uploaded()
                    a_and_q = posted.split(b'?', 1)
                    args = a_and_q[0][len(fn):].split(b'/')[1:]
                    qs_pairs = _qsp(a_and_q[1]) if (len(a_and_q) > 1) else []
                    del kwargs['method']

                kwargs.update(dict(
                    (str(p[0], 'latin-1'), dumb_decode(p[1]))
                    for p in qs_pairs))

                self._caller = dumb_decode(args.pop(0)) if args else None
                client_info_tuple[0] = self._caller
                kwargs['reply_kwargs'] = {
                    'client_info_tuple': client_info_tuple}

                if argdecode:
                    args = [dumb_decode(a) for a in args]
                rv = func(*args, **kwargs)
                if inspect.isawaitable(rv):
                    rv = await rv

                t = 1000 * (time.time() - t0)
                stats = self.status
                stats[fn+'_ok'] = stats.get(fn+'_ok', 0) + 1
                stats[fn+'_ms'] = 0.95*stats.get(fn+'_ms', t) + 0.05*t
                stats['requests_ok'] += 1
                return rv
            except APIException as e:
                return async_reply_json(e.as_dict(), http_code=self.HTTP_424)
            except TypeError:
                logging.exception('Error in RPC handler %s %s(%s, %s)'
                    % (method, fn, args, kwargs))
                if kwargs:
                    self.status['requests_ignored'] += 1
                    return async_reply(self.HTTP_400)  # This is a guess :-(
            except KeyboardInterrupt:
                pass
            except:
                logging.exception('Error in RPC handler %s %s(%s, %s)'
                    % (method, fn, args, kwargs))
            self.status['requests_failed'] += 1
            async_reply(self.HTTP_500)
        else:
            logging.debug('Unknown method: %s' % (fn,))
            self.status['requests_ignored'] += 1
            async_reply(self.HTTP_404)

    def common_rpc_handler(self,
            fn, method, args, qs_pairs, prep, uploaded):
        t0 = time.time()
        argdecode_and_func = self.functions.get(fn)
        fn = str(fn, 'latin-1')
        if argdecode_and_func is not None:
            try:
                argdecode, func = argdecode_and_func
                kwargs = prep(method)

                # Support arbitrarily large arguments, via POST
                if method == 'POST' and (len(args) == 1) and (args[0] == b'*'):
                    posted = uploaded()
                    a_and_q = posted.split(b'?', 1)
                    args = a_and_q[0][len(fn):].split(b'/')[1:]
                    qs_pairs = _qsp(a_and_q[1]) if (len(a_and_q) > 1) else []
                    del kwargs['method']

                kwargs.update(dict(
                    (str(p[0], 'latin-1'), dumb_decode(p[1]))
                    for p in qs_pairs))

                self._caller = dumb_decode(args.pop(0)) if args else None
                if argdecode:
                    args = [dumb_decode(a) for a in args]
                rv = func(*args, **kwargs)

                t = 1000 * (time.time() - t0)
                stats = self.status
                stats[fn+'_ok'] = stats.get(fn+'_ok', 0) + 1
                stats[fn+'_ms'] = 0.95*stats.get(fn+'_ms', t) + 0.05*t
                stats['requests_ok'] += 1
                return rv
            except APIException as e:
                return self.reply_json(e.as_dict(), http_code=self.HTTP_424)
            except TypeError:
                logging.exception('Error in RPC handler %s %s(%s, %s)'
                    % (method, fn, args, kwargs))
                if kwargs:
                    self.status['requests_ignored'] += 1
                    return self.reply(self.HTTP_400)  # This is a guess :-(
            except KeyboardInterrupt:
                pass
            except:
                logging.exception('Error in RPC handler %s %s(%s, %s)'
                    % (method, fn, args, kwargs))
            self.status['requests_failed'] += 1
            self.reply(self.HTTP_500)
        else:
            logging.debug('Unknown method: %s' % (fn,))
            self.status['requests_ignored'] += 1
            self.reply(self.HTTP_404)


class WorkerPool:
    def __init__(self, workers):
        self.lock = threading.RLock()
        self.count = 0
        self.workers = []
        self.caller_lock = threading.Lock()
        self.caller_info = None
        self.quit = lambda: self._proxy_all('quit')
        self.join = lambda *a: self._proxy_all('join', args=a)
        self.terminate = lambda: self._proxy_all('terminate')
        for caps, cls, args, kwargs in workers:
            self.add_worker(caps, cls, args, kwargs)

    def housekeeping(self):
        with self.lock:
            for w_tuple in self.workers:
                worker = w_tuple[2]
                if worker and (worker.exitcode is not None):
                    try:
                        worker.join()
                    except:
                        pass
                    w_tuple[2] = None

    def forked(self):
        with self.lock:
            for i, (name, cap, worker, cak, ts) in enumerate(self.workers):
                if worker:
                    self.workers[i][2] = None

    def add_worker(self, caps, cls, args, kwargs):
        with self.lock:
            kwargs = copy.copy(kwargs)
            if 'name' in kwargs:
                name = '%s_%d_%d' % (kwargs['name'], os.getpid(), self.count)
                kwargs['name'] = name
            else:
                name = None

            worker = cls(*args, **kwargs)
            if name is None:
                name = '%s_%d_%d' % (worker.name, os.getpid(), self.count)

            self.workers.append([name, caps, worker, (cls, args, kwargs), 0])
            self.count += 1

    def auto_add_worker(self, pop, which, capabilities):
        logging.error('Worker not found: %s/%s' % (which, capabilities))
        return None

    def with_worker(self, which=None, capabilities='', pop=False, wait=False):
        for tries in range(0, 50):
            worker = name = cap = None
            with self.lock:
                for i, (name, cap, worker, cak, ts) in enumerate(self.workers):
                    if (((not which) or name.startswith(which))
                            and (capabilities in cap)):
                        if worker:
                            worker = worker.connect(quick=(ts > 0))
                        if not worker:
                            cls, args, kwargs = cak
                            worker = cls(*args, **kwargs)
                            self.workers[i][2] = worker
                            worker = worker.connect(quick=False)

                        if worker:
                            if pop:
                                return self.workers.pop(i)
                            else:
                                return worker

            logging.debug('Adding new worker: %s/%s' % (which, capabilities))
            worker = self.auto_add_worker(pop, which, capabilities)
            if worker or not wait:
                w = (worker[2] if pop else worker).connect(quick=False)
                if w or not wait:
                    return worker

            # Busy waits are bad...?
            time.sleep(0.05)

    def choose_worker(self, pop, wait, fn, args, kwargs):
        """
        This function exists so subclasses can override it and make choices
        based on the function name and arguments.
        """
        return self.with_worker(pop=pop, wait=wait)

    def connect(self, *args, **kwargs):
        # FIXME: This is a noop, real connect() happens later. This may
        #        suck for error handling.
        return self

    def with_caller(self, caller):
        if caller:
            self.caller_lock.acquire()
            self.caller_info = caller
        return self

    def get_caller(self):
        if self.caller_info:
            try:
                return self.caller_info
            finally:
                self.caller_info = None
                self.caller_lock.release()
        return None

    def call(self, fn, *args, **kwa):
        caller = self.get_caller()
        w_tuple = None
        for tries in range(0, 2):
            try:
                w_tuple = self.choose_worker(True, True, fn, args, kwa)
                worker = w_tuple[2]
                if caller:
                    return worker.with_caller(caller).call(fn, *args, **kwa)
                else:
                    return worker.call(fn, *args, **kwa)
            except socket.error as e:
                logging.exception('socket.error in WorkerPool.call')
                w_tuple[2] = None
            finally:
                if w_tuple:
                    w_tuple[-1] = time.time()
                    self.workers.append(w_tuple)

    async def async_call(self, loop, fn, *args, **kwa):
        caller = self.get_caller()
        w_tuple = None
        for t1 in range(0, 2):
            try:
                for t2 in range(0, 50):
                    w_tuple = self.choose_worker(True, False, fn, args, kwa)
                    if w_tuple:
                        break
                    await asyncio.sleep(0.05)
                worker = w_tuple[2]
                if caller:
                    return await worker.with_caller(caller).async_call(
                        loop, fn, *args, **kwa)
                else:
                    return await worker.async_call(loop, fn, *args, **kwa)
            except socket.error as e:
                logging.exception('socket.error in WorkerPool.async_call')
                w_tuple[2] = None
            finally:
                if w_tuple:
                    w_tuple[-1] = time.time()
                    self.workers.append(w_tuple)

    def is_alive(self):
        with self.lock:
            for name, _, worker, _, _ in self.workers:
                if worker and worker.is_alive():
                    return True
        return False

    def _proxy_all(self, method, autostart=False, args=set(), kwargs={}):
        with self.lock:
            for name, _, worker, _, _ in self.workers:
                func = getattr(worker, method) if worker else None
                if func is not None:
                    if worker.connect(autostart=autostart, quick=True):
                        try:
                            func(*args, **kwargs)
                        except (IOError, OSError):
                            pass


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)

    class TestWorker(BaseWorker):
        LOG_STDOUT = True
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
                except KeyboardInterrupt:
                    pass
                except:
                    logging.exception('Ping failed!')
            self.reply_json({pong: args})

    tw = WorkerPool([
            ('test', TestWorker, ('/tmp',), {'name': 'moggie-test-worker'}),
        ]).connect()
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
            print('** Terminating **')
            tw.terminate()
