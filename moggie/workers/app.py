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
from ..util.dumbcode import to_json, from_json
from .public import PublicWorker, RequestTimer


@async_url('/')
@http_require(secure_transport=True, csrf=False)
async def web_root(req_env):
    try:
        cmd = await CLI_COMMANDS.get('welcome').WebRunnable(
            req_env['worker'],
            req_env['worker'].get_auth(req_env,
                allow_anonymous=True,
                secure_transport=True),
            req_env['frame'],
            req_env['conn'],
            req_env, [])

        asyncio.get_event_loop().create_task(cmd.web_run())
        return {'mimetype': 'text/html; charset="utf-8"', 'eof': False}
    except Exception as e:
        logging.exception('web_root failed: %s' % e)
        return {'code': 500, 'msg': 'Failed', 'body': 'Sorry\n'}


@async_url('/cli/*')
@http_require(secure_transport=True, csrf=False)
@process_post(max_bytes=2048000, _async=True)
async def web_cli(req_env):
    conn = req_env['conn']
    frame = req_env['frame']

    # FIXME: Have we completely fucked up our CSRF? Any commands doing
    #        exciting stuff should reject GET. This needs urgent checking.

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

    headers = {}
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
    if cmd.disposition or cmd.filename:
        disp = cmd.disposition or 'attachment'
        if cmd.filename:
            disp += '; filename="%s"' % cmd.filename
        headers['Content-Disposition'] = disp

    return {'mimetype': cmd.mimetype, 'eof': False, 'hdrs': headers}


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
        await conn.send(to_json({'connected': 1}))  #FIXME

    if msg:
        code = 500
        result = {}
        web_access = conn.env['access']
        conn_uid = conn.uid
        try:
            result = await conn.env['app'].api_request(
                conn_uid, web_access, from_json(msg))
            code = result.get('code', 500)
            if code == 200 and 'body' in result:
                await conn.send(result['body'])
                return
        except:
            logging.exception('websocket failed: %s' % (msg,))
        await conn.send(to_json({'error': code, 'result': result}))  #FIXME


# WARNING: We don't actually implement JMAP properly yet! This is part of
#          an un-concluded experiment in that direction.
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


@async_url('/api')
@http_require(csrf=False)
@process_post(max_bytes=204800, _async=True)
# FIXME: Should this also be a websocket? How do JMAP websockets work?
async def web_api(req_env):
    code, msg, status = 500, 'Oops', 'err'
    with RequestTimer('web_api', req_env, status='rej') as timer:
        try:
            access = req_env['worker'].get_auth(req_env, secure_transport=True)
            timer.status = 'ok'
            # FIXME: Do we want more granularity on our timers? If so, we need
            #        to change the timer name to match the method(s) called.
            #timer.name = 'api_foo'
            if req_env.post_data:
                return await req_env['app'].api_request(
                    None, access, req_env.post_data)
        except PermissionError as e:
            code, msg, status = 403, str(e), 'rej'
        except:
            logging.exception('web_api failed')

        # If we get this far, we had an internal error of some sort.
        timer.status = status
        return {'code': code, 'msg': msg, 'body': 'Sorry\n'}


@url('/favicon.ico')
def web_favico(req_env):
    return req_env['app'].get_static_asset('img/favicon.png')


@url('/static/*')
def web_static(req_env):
    try:
        # len('/static/') == 8
        return req_env['app'].get_static_asset(req_env.request_path[8:])
    except:
        return {'code': 404, 'ttl': 3600, 'msg': 'Not Found'}


@url('/themed/*')
def web_themed(req_env):
    try:
        # len('/themed/') == 8
        return req_env['app'].get_static_asset(req_env.request_path[8:],
            themed=True)
    except:
        logging.exception('Theming failed')
        return {'code': 404, 'ttl': 3600, 'msg': 'Not Found'}


class AppWorker(PublicWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'app'
    PUBLIC_PATHS = ['/api', '/ws', '/', '/favicon.ico']
    PUBLIC_PREFIXES = ['/pile', '/static/', '/themed/', '/.well-known/',
                       '/cli/help', '/cli/show']
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

    async def async_api_request(self, access, request_obj):
        if self._sock:
            return await self.app.api_request(
                None, access, request_obj, internal=True)

        # FIXME: It would be nice if this were async too...
        if (access is True) or (access and
                access.config_key == self.app.config.ACCESS_ZERO):
            return self.call('rpc/api', request_obj)
        else:
            raise PermissionError('Access denied')

    def api_request(self, access, request_obj):
        if (access is True) or (access and
                access.config_key == self.app.config.ACCESS_ZERO):
            return self.call('rpc/api', request_obj)
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
        await ws_broadcast('app', to_json(message), only=ofunc)

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
