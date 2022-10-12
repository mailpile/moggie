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
import base64
import json
import logging
import os
import sys
import traceback

from upagekite.proto import asyncio, fuzzy_sleep_ms
from upagekite.httpd import HTTPD, url, async_url
from upagekite.web import process_post, http_require, access_requires
from upagekite.websocket import websocket, ws_broadcast

from ..app.cli import CLI_COMMANDS
from ..app.core import AppCore
from .public import PublicWorker, RequestTimer


@async_url('/cli/*')
@http_require(secure_transport=True, csrf=False)
@process_post(max_bytes=2048000, _async=True)
async def web_cli(req_env):
    conn = req_env['conn']
    frame = req_env['frame']

    args = req_env.request_path.split('/')
    while args.pop(0) != 'cli':
        pass
    if not args or (args == ['']):
        args = ['help']

    command = CLI_COMMANDS.get(args.pop(0))
    if not (hasattr(command, 'WEB_EXPOSE') and command.WEB_EXPOSE):
        return {'code': 404, 'msg': 'No such command'}

    access = req_env['worker'].get_auth(req_env,
        allow_anonymous=(hasattr(command, 'ROLES') and not command.ROLES),
        secure_transport=True)

    post_vars = req_env.post_vars
    if 'argz' in post_vars:
        argz = post_vars.get('argz')
        if isinstance(argz, dict):
            argz = argz['value']
        if argz:
            # All argz args end with \0, so we know split gives us one
            # too many for sure. Thus the -1.
            argz = argz.split('\0')[:-1]
            # We also know there may be a trailing blank arg, due to how
            # --stdin= is implemented. So this sucks a bit, but is usually
            # a win.
            if argz and not argz[-1]:
                argz.pop(-1)
            args.extend(argz)
    else:
        for var, val in (
                list(req_env.query_tuples) +
                list(post_vars.items())):
            if isinstance(val, dict):
                val = val['value']
            if val == 'True':
                args.append('--%s' % var)
            else:
                args.append('--%s=%s' % (var, val))

    try:
        cmd = await command.WebRunnable(
            req_env['worker'], access, frame, conn, req_env, args)
    except PermissionError as e:
        logging.debug('PermissionError in WebRunnable: %s' % e)
        return {'code': 403, 'msg': str(e), 'body': str(e)}

    asyncio.get_event_loop().create_task(cmd.web_run())
    return {'mimetype': cmd.mimetype, 'eof': False}


@async_url('/')
@http_require(secure_transport=True, csrf=False)
async def web_root(req_env):
    with RequestTimer('web_root', req_env):
        try:
            return {
                'body': await req_env['app'].api_webroot(req_env),
                'mimetype': 'text/html; charset="utf-8"'}
        except Exception as e:
            logging.exception('web_root failed: %s' % e)
            return {'code': 500, 'msg': 'Failed', 'body': 'Sorry\n'}


def websocket_auth_check(req_env):
    access = req_env['worker'].get_auth(req_env, secure_transport=True)
    if not access:
        raise PermissionError('Access Denied')
    req_env['access'] = access


@async_url('/pile', '/pile/*')
@http_require(secure_transport=True, csrf=False)
@process_post(max_bytes=204800, _async=True)
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
    code, msg, status = 500, 'Oops', 'err'
    with RequestTimer('web_pile', req_env, status='rej') as timer:
        try:
            auth = req_env['worker'].get_auth(req_env)
            timer.status = 'ok'
            return await req_env['app'].api_treeview(None, auth)
        except PermissionError as e:
            code, msg, status = 403, str(e), 'rej'
        except:
            logging.exception('web_pile failed')

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


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
        web_access = conn.env['access']
        conn_uid = conn.uid
        try:
            result = await conn.env['app'].api_jmap(
                conn_uid, web_access, json.loads(msg))
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
            access = req_env['worker'].get_auth(req_env, post=False, secure_transport=True)
            timer.status = 'ok'
            return await req_env['app'].api_jmap_session(access)
        except PermissionError as e:
            code, msg, status = 403, str(e), 'rej'
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
            access = req_env['worker'].get_auth(req_env, secure_transport=True)
            timer.status = 'ok'
            # FIXME: Do we want more granularity on our timers? If so, we need
            #        to change the timer name to match the method(s) called.
            #timer.name = 'jmap_foo'
            if req_env.post_data:
                return await req_env['app'].api_jmap(
                    None, access, req_env.post_data)
        except PermissionError as e:
            code, msg, status = 403, str(e), 'rej'
        except:
            logging.exception('web_jmap failed')

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


@url('/favicon.ico')
def web_favico(req_env):
    global FAVICON_M
    return FAVICON_M


class AppWorker(PublicWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'app'
    PUBLIC_PATHS = ['/jmap', '/ws', '/', '/favicon.ico']
    PUBLIC_PREFIXES = ['/pile', '/.well-known/', '/cli/help', '/cli/show']
    CONFIG_SECTION = 'App'

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
            args.append('--wait')
        return obj

    def connect(self, *args, **kwargs):
        conn = super().connect(*args, **kwargs)
        if conn:
            self.auth_token = self.call('rpc/get_access_token')['token']
            self.set_rpc_authorization('Bearer %s' % self.auth_token)
        return conn

    async def async_jmap(self, access, request_obj):
        if self._sock:
            return await self.app.api_jmap(
                None, access, request_obj, internal=True)

        # FIXME: It would be nice if this were async too...
        if (access is True) or (access and
                access.config_key == self.app.config.ACCESS_ZERO):
            return self.call('rpc/jmap', request_obj)
        else:
            raise PermissionError('Access denied')

    def jmap(self, access, request_obj):
        if (access is True) or (access and
                access.config_key == self.app.config.ACCESS_ZERO):
            return self.call('rpc/jmap', request_obj)
        else:
            raise PermissionError('Access denied')

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

    def _check_access(self, secret, path):
        if (secret == self._secret):
            return self.app.config.access_zero()
        return self.app.config.access_from_token(str(secret, 'utf-8'),
            _raise=False)

    def get_auth(self, req_env, allow_anonymous=False, **req_kwargs):
        # Set req_env[auth_*], or raise PermissionError
        access_requires(req_env, **req_kwargs)
        req_acl = req_env.get('access')
        if 'auth_basic' in req_env:
            # FIXME: If we have a username and password, yay
            username, password = req_env['auth_basic']
            logging.warning('FIXME: BASIC AUTH')
        elif 'auth_bearer' in req_env:
            acl = self.app.config.access_from_token(req_env['auth_bearer'])
            logging.debug('Access: %s = %s' % (acl.config_key, acl))
            return acl
        elif req_acl:
            logging.debug('Access: %s' % (req_acl,))
            return req_acl
        elif allow_anonymous:
            return None
        raise PermissionError('Please login')


FAVICON_M = {
    'ttl': 7200,
    'mimetype': 'image/png',
    'body': base64.b64decode("""\
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
AAAAAElFTkSuQmCC""")}


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
