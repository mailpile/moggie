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


@async_url('/ws')
@websocket('app')
async def web_websocket(opcode, msg, conn, ws,
                        first=False, eof=False, websocket=True):
    if not websocket:
        return {'code': 400, 'body': 'Sorry\n'}

    # FIXME: Incoming messages should be JMAP requests; but we need
    #        to tag them with info about which user made the request.
    web_user = None
    request = {}
    # FIXME


@async_url('/jmap')
@process_post(max_bytes=204800, _async=True)
async def web_jmap(req_env):
    with RequestTimer('web_jmap', req_env, status='rej') as timer:
        require(req_env, secure=True)
        timer.status = 'ok'
        try:
            # FIXME: Incoming messages should be JMAP requests; but we need
            #        to tag them with info about which user made the request.
            web_user = None

            # FIXME: Do we want more granularity on our timers? If so, we need
            #        to change the timer name to match the method(s) called.
            #timer.name = 'jmap_foo'
            if req_env.post_data:
                return await req_env['app'].api_jmap(web_user, req_env.post_data)
        except:
            traceback.print_exc()

        # If we get this far, we had an internal error of some sort.
        timer.status = 'err'
        return {'code': 500, 'body': 'Sorry\n'}


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

    def get_app(self):
        return AppCore(self)

    def websocket_url(self):
        return 'ws' + self.url[4:] + '/rpc'

    def startup_tasks(self):
        self.app.startup_tasks()

    def shutdown_tasks(self):
        self.app.shutdown_tasks()


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
