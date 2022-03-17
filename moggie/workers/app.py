# This is the main public-facing web service for the moggie backend.
#
# It serves a requests that fall into roughly three categories:
#
#    - Public, unauthenticated requests (login page)
#    - Local app-internal RPC calls
#    - Public authenticated requests
#
# Internal RPC calls are authenticated by secrets in the URL string
# itself, which are in turn stored in files in the user's home directory
# which only that user (and root) can access. This allows CLI tools to
# communicate with the running app without authenticating every time.
#
import json
import os
import sys
import traceback

from upagekite.httpd import HTTPD, url, async_url
from upagekite.web import process_post
from upagekite.websocket import websocket

from ..app.core import AppCore
from .public import PublicWorker, RequestTimer, require


@async_url('/')
async def web_root(req_env):
    with RequestTimer('web_root', req_env):
        require(req_env, post=False, secure=True)
        try:
            return self.app_root()
        except:
            return {'code': 500, 'body': 'Sorry\n'}


def websocket_auth_check(req_env):
    auth = req_env['worker'].get_auth(req_env, post=False, secure=True)
    if not auth:
        raise PermissionError('Access Denied')
    req_env['auth'] = auth


@async_url('/ws')
@websocket('app', auth_check=websocket_auth_check)
async def web_websocket(opcode, msg, conn, ws,
                        first=False, eof=False, websocket=True):
    if not websocket:
        return {'code': 400, 'body': 'Sorry\n'}

    if first:
        await conn.send(json.dumps({'connected': 1}))  #FIXME

    if msg:
        code = 500
        web_auth = conn.env['auth']
        try:
            print('%s' % msg)
            result = await conn.env['app'].api_jmap(web_auth, json.loads(msg))
            code = result.get('code', 500)
            if code == 200 and 'body' in result:
                await conn.send(result['body'])
                return
        except:
            traceback.print_exc()
            pass
        await conn.send(json.dumps({'error': code}))  #FIXME


@async_url('/.well-known/jmap')
@process_post(max_bytes=20480, _async=True)
async def web_jmap_session(req_env):
    code, msg, status = 500, 'Oops', 'err'
    with RequestTimer('web_jmap_session', req_env, status='rej') as timer:
        try:
            auth = req_env['worker'].get_auth(req_env, post=False, secure=True)
            timer.status = 'ok'
            return await req_env['app'].api_jmap_session(auth)
        except PermissionError:
            code, msg, status = 403, 'Access Denied', 'rej'
        except:
            traceback.print_exc()

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


@async_url('/jmap')
@process_post(max_bytes=204800, _async=True)
# FIXME: Should this also be a websocket? How do JMAP websockets work?
async def web_jmap(req_env):
    code, msg, status = 500, 'Oops', 'err'
    with RequestTimer('web_jmap', req_env, status='rej') as timer:
        try:
            auth = req_env['worker'].get_auth(req_env, secure=True)
            timer.status = 'ok'
            # FIXME: Do we want more granularity on our timers? If so, we need
            #        to change the timer name to match the method(s) called.
            #timer.name = 'jmap_foo'
            if req_env.post_data:
                return await req_env['app'].api_jmap(auth, req_env.post_data)
        except PermissionError:
            code, msg, status = 403, 'Access Denied', 'rej'
        except:
            traceback.print_exc()

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


@async_url('/recovery_svc/*')
@process_post(max_bytes=2048, _async=True)
async def proxy_recovery_svc(req_env):
    with RequestTimer('web_recovery_svc', req_env) as timer:
        require(req_env, post=True, secure=True)
        posted = req_env.post_data
        print('FIXME: Proxy to recovery_svc worker' % posted)
        return {'code': 500, 'body': 'Proxy Not Ready\n'}


class AppWorker(PublicWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'app'
    PUBLIC_PATHS = ['/']
    PUBLIC_PREFIXES = ['/recovery_svc']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.functions.update(self.app.rpc_functions)
        self.sessions = {}
        self.auth_token = None

    def connect(self, *args, **kwargs):
        conn = super().connect(*args, **kwargs)
        self.auth_token = self.call('rpc/get_access_token')['token']
        self.set_rpc_authorization('Bearer %s' % self.auth_token)
        return conn

    def get_app(self):
        return AppCore(self)

    def websocket_url(self):
        return 'ws' + self.url[4:] + '/ws'

    def startup_tasks(self):
        self.app.startup_tasks()

    def shutdown_tasks(self):
        self.app.shutdown_tasks()

    def get_auth(self, req_env, **req_kwargs):
        req_info = require(req_env, **req_kwargs)
        # If cookie is in session list, we know who this is
        # If we have a username and password, yay
        print('req_info = %s' % (req_info,))
        if 'auth_basic' in req_info:
            print('FIXME: BASIC AUTH')
        elif 'auth_bearer' in req_info:
            acl = self.app.config.access_from_token(req_info['auth_bearer'])
            print('Access: %s = %s' % (acl.config_key, acl))
            return acl
        raise PermissionError('Please login')


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
