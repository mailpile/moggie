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
import logging
import os
import sys
import traceback

from upagekite.httpd import HTTPD, url, async_url
from upagekite.web import process_post, http_require, access_requires
from upagekite.websocket import websocket, ws_broadcast

from ..app.core import AppCore
from .public import PublicWorker, RequestTimer


@async_url('/')
@http_require(secure_transport=True, csrf=False)
async def web_root(req_env):
    with RequestTimer('web_root', req_env):
        try:
            return self.app_root()
        except:
            return {'code': 500, 'body': 'Sorry\n'}


def websocket_auth_check(req_env):
    auth = req_env['worker'].get_auth(req_env, secure_transport=True)
    if not auth:
        raise PermissionError('Access Denied')
    req_env['auth'] = auth


@async_url('/pile', '/pile/*')
async def web_treeview(req_env):
    # The idea here would be to expose search results, tags and the outbox
    # as virtual Maildirs over HTTP. And pretty much anything else we want
    # the user to have access to... As a read-only resource (plus outbox),
    # this is simple. If we want to support writes, things get weird fast
    # if we want to support only normal filesystem semantics and HTTP verbs.
    #
    # URL ideas:
    #   /pile/mail/                            -> list of tags / searches
    #   /pile/mail/search term/search-term.zip -> dump of all matching mail
    #   /pile/mail/search term/cur/            -> results
    #   /pile/mail/search term/cur/12345       -> individual message
    #   /pile/mail/search term/cur/12345/3     -> message part (attachment?)
    #   /pile/contacts/all-contacts.zip        -> dump of all contacts
    #   /pile/contacts/user@foo.vcard          -> individual contact
    #   ...?as=json                            -> change output format?
    #
    return {
        'code': 200,
        'body': 'Hello world\n'}


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
        result = {}
        web_auth = conn.env['auth']
        conn_uid = conn.uid
        try:
            result = await conn.env['app'].api_jmap(
                conn_uid, web_auth, json.loads(msg))
            code = result.get('code', 500)
            if code == 200 and 'body' in result:
                await conn.send(result['body'])
                return
        except:
            logging.exception('websocket failed: %s' % (msg,))
        await conn.send(json.dumps({'error': code, 'result': result}))  #FIXME


@async_url('/.well-known/jmap')
@http_require(csrf=False)
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
            logging.exception('web_jmap_session failed')

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


@async_url('/jmap')
@http_require(csrf=False)
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
                return await req_env['app'].api_jmap(
                    None, auth, req_env.post_data)
        except PermissionError:
            code, msg, status = 403, 'Access Denied', 'rej'
        except:
            logging.exception('web_jmap failed')

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


class AppWorker(PublicWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'app'
    PUBLIC_PATHS = ['/']
    PUBLIC_PREFIXES = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.functions.update(self.app.rpc_functions)
        self.sessions = {}
        self.auth_token = None
        self.want_cli = False

    @classmethod
    def FromArgs(cls, workdir, args):
        opts = [a for a in args if a[:2] == '--']
        for opt in opts:
            args.remove(opt)

        obj = super().FromArgs(workdir, args)
        if '--cli' in opts:
            obj.want_cli = True
            args.append('wait')
        return obj

    def connect(self, *args, **kwargs):
        conn = super().connect(*args, **kwargs)
        if conn:
            self.auth_token = self.call('rpc/get_access_token')['token']
            self.set_rpc_authorization('Bearer %s' % self.auth_token)
        return conn

    def jmap(self, request_obj):
        return self.call('rpc/jmap', request_obj)

    def get_app(self):
        return AppCore(self)

    def websocket_url(self):
        return 'ws' + self.url[4:] + '/ws'

    def startup_tasks(self):
        self.app.startup_tasks()
        if self.want_cli:
            self.app.start_pycli()

    def shutdown_tasks(self):
        self.app.shutdown_tasks()

    async def broadcast(self, message, only=None):
        if isinstance(only, list):
            ofunc = lambda wss: (wss.uid in only)
        elif isinstance(only, str):
            ofunc = lambda wss: (wss.uid == only)
        else:
            ofunc = only
        await ws_broadcast('app', json.dumps(message), only=ofunc)

    def get_auth(self, req_env, **req_kwargs):
        # Set req_env[auth_*], or raise PermissionError
        access_requires(req_env, **req_kwargs)
        if 'auth_basic' in req_env:
            # FIXME: If we have a username and password, yay
            username, password = req_env['auth_basic']
            logging.warning('FIXME: BASIC AUTH')
        elif 'auth_bearer' in req_env:
            acl = self.app.config.access_from_token(req_env['auth_bearer'])
            logging.debug('Access: %s = %s' % (acl.config_key, acl))
            return acl
        raise PermissionError('Please login')


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    aw = AppWorker('/tmp').connect()
    if aw:
        try:
            print('** We are live, yay')
            #print(aw.capabilities())
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
