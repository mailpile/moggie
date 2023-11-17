import asyncio
import logging
import sys
import time

from .app.cli.exceptions import NotRunning, Nonsense
from .api.requests import RequestCommand
from .util.dumbcode import from_json, to_json


SHARED_MOGGIES = {}


def set_shared_moggie(moggie, which='default'):
    """
    Configure a moggie object for access via `get_shared_moggie()`.
    """
    SHARED_MOGGIES[which if which else moggie.name] = moggie
    return moggie


def get_shared_moggie(which='default'):
    """
    Fetch a globally shared moggie object, either the default or
    the one specified by the `which` argument.
    """
    return SHARED_MOGGIES.get(which)


class Moggie:
    """
    This is the public Moggie API, in its Pythonic form.

    An instance of this class (usually) represents an authenticated
    session with a running moggie, although some methods (including
    `email` and `parse`) may be run without a backend. The moggie
    backend may be remote or local, or internal to the running Python
    environment.

    Example:

        moggie = Moggie(url='https://...').connect()

        moggie.set_output(sys.stdout)
        moggie.search('bjarni', limit=5)

        buffer = []
        moggie.set_output(buffer, eof_marker='\n## EOF ##\n')
        moggie.set_access(access)  # An access token or URL
        await moggie.async_search('bjarni', limit=5)

    Pub/sub example:

        await moggie.enable_websocket(event_loop)
        moggie.subscribe(..., callback_func)
        moggie.search('bjarni', limit=5, on_success=callback_func)

        # FIXME: Define a subscribe method, for handling misc. events.

    Moggie's API methods can be invoked either synchronously, or async.
    The async methods have a `async_` prefix to the method names.

    API methods will either return their output buffer on success
    (raising exceptions for certain errors), or invoke the named
    callback functions (`on_success=...` or `on_error=...`) when
    completed.

    NOTE: The synchronous methods use the async code behind the
          scenes, but cannot await completion if there is already an
    event loop running. This means the output buffer may get populated
    at an unexpected time, well after the synchronious method has
    returned. To avoid confusion, using async methods is preferred.

    The `Moggie.set_` methods always return the moggie object itself,
    making it easy to chain things together. An example:

        moggie.set_output(sys.stdout).search('bjarni', format='json')

    FIXME: Also define a callback-based mode of operations so I can
           actually write the TUI using this as well. Then things
           finally come together?
    """
    MODE_CLI = 'cli'
    MODE_PYTHON = 'py'
    DEFAULT_MODE = 'py'
    DEFAULT_LOG_LEVEL = 2
    PREFER_ASYNC = False

    @classmethod
    def Setup(cls):
        import moggie.sys_path_helper
        from .app.cli import CLI_COMMANDS
        if 'default' not in CLI_COMMANDS:
            # Merge the app COMMANDS with the CLI_COMMANDS registry.
            # Trust me, I know what I'm doing!
            from . import app
            CLI_COMMANDS.update({
                'default': app.CommandMuttalike,
                'restart': app.CommandRestart,
                'start': app.CommandStart,
                'stop': app.CommandStop,
                'tui': app.CommandTUI})
            app.COMMANDS = CLI_COMMANDS
        cls._COMMANDS = CLI_COMMANDS

        cls._PRE_HOOKS = {
            'start': cls._pre_start,
            'restart': cls._pre_start}
        cls._HOOKS = {
            'start': cls._on_start,
            'restart': cls._on_start,
            'stop': cls._on_stop}

        import base64
        def _kwas_to_args(kwa):
            kwa_keys = list(kwa.keys())
            for k in kwa_keys:
                if k in ('on_success', 'on_error', 'moggie_wrap'):
                    continue

                v = kwa.pop(k)
                if k[:1] == '_':
                    k = k[1:]
                k = k.replace('_', '-')
                if isinstance(v, bool):
                    if v:
                        yield '--%s' % k
                elif isinstance(v, bytes):
                    # FIXME: It is pretty gross that we need to do this within
                    #        Python; but the fact that it is POSSIBLE is good
                    # for the CLI API itself. Binary is hard!
                    yield ('--%s=base64:%s' %
                        (k, str(base64.b64encode(v), 'utf-8')))
                else:
                    yield '--%s=%s' % (k, v)

        def _fix_args(a, kwa):
            args = list(_kwas_to_args(kwa))
            args.extend(a)
            return args, kwa

        def _mk_method(cmd):
            def _sync_runner(self, *a, **kwa):
                a, kwa = _fix_args(a, kwa)
                return self.run(cmd, *a, **kwa)
            return _sync_runner

        def _mk_async_method(cmd):
            async def _async_runner(self, *a, **kwa):
                a, kwa = _fix_args(a, kwa)
                return await self.async_run(cmd, *a, **kwa)
            return _async_runner

        for command in cls._COMMANDS:
            name = command.replace('-', '_')
            if name in ('import',):
                name += '_'

            sync_method = _mk_method(command)
            async_method =  _mk_async_method(command)

            setattr(cls, 'async_' + name, async_method)
            setattr(cls, 'sync_' + name, sync_method)
            setattr(cls, name, async_method if cls.PREFER_ASYNC else sync_method)

    @classmethod
    def _can_run_async(cls, command):
        return not (command and not hasattr(command, 'Command'))

    def __init__(self,
            work_dir=None,
            app=None,
            app_worker=None,
            access=True,
            mode=None,
            name=None):

        from .config.paths import AppConfig, DEFAULT_WORKDIR

        self.name = name or ('moggie-%x' % int(time.time()))
        if work_dir and app_worker:
            raise Nonsense('Specify work_dir or app_worker, not both')
        elif work_dir is None:
            work_dir = DEFAULT_WORKDIR()

        if app_worker or app:
            self.work_dir = app_worker.profile_dir
            self._app = app or app_worker.get_app()
            self._app_worker = app_worker
            self._config = self._app.config
        else:
            self.work_dir = work_dir
            self._app = None
            self._app_worker = None
            self._config = AppConfig(work_dir)

        if access is True:
            access = self._config.access_zero()

        self._async_tasks =  []
        self._ws_callbacks = {}
        self._ws_failed = []
        self._ev_loop = None
        self._bridge = None
        self._name = 'moggie@%d' % int(time.time())

        self.loop = asyncio.get_event_loop()
        self.set_input()
        self.set_access(access)
        self.set_mode(mode or self.DEFAULT_MODE)

    async def enable_websocket(self, ev_loop):
        from moggie.util.rpc import AsyncRPCBridge
        self._ev_loop = ev_loop
        AsyncRPCBridge(ev_loop, self._name, self._app_worker, self)
        sleeptime, deadline = 0, (time.time() + 10)
        while time.time() < deadline:
            sleeptime = min(sleeptime + 0.01, 0.1)
            await asyncio.sleep(sleeptime)
            if self._bridge is not None:
                break

    def link_bridge(self, bridge):
        def _receive_message(bridge_name, raw_message):
            message = from_json(raw_message)
            if message.get('connected'):
                self._bridge = bridge
            else:
                self._handle_ws_message(message)
        return _receive_message

    def _handle_ws_message(self, message):
        req_id = message.get('req_id', None)

        if req_id:
            hooks = self._ws_callbacks.pop(req_id, [])
        elif ('error' in message) and ('request' in message):
            #
            # If this is an error, we do not remove the request handlers
            # from self._ws_callbacks - this allows the caller to retry
            # using the same req_id and everything should work. We do add
            # a record to self._ws_failed so things get cleaned up "later".
            #
            req_id = message['request'].get('req_id')
            hooks = self._ws_callbacks.get(req_id, [])
            self._ws_failed.append((time.time(), req_id))
        else:
            hooks = []

        hooks.extend(self._ws_callbacks.get(message.get('req_type')) or [])
        hooks.extend(self._ws_callbacks.get('*') or [])
        handled = 0
        for call_spec in hooks:
           on_success, on_error = call_spec[-2:]
           try:
               if 'internal_websocket_error' in message:
                   logging.error('FIXME: Handle internal error: %s' % message)
                   handled += 1
               elif 'error' in message:
                   if on_error:
                       on_error(self, message)
                       handled += 1
               elif on_success:
                   if 'data' in message and ('_RAW_' not in call_spec):
                       on_success(self, message['data'])
                   else:
                       on_success(self, message)
                   handled += 1
           except Exception as e:
               try:
                   if on_error:
                       on_error(self, message, e)
                       continue
               except:
                   pass
               logging.exception(
                   'Error handling message: %s <= %s' % (call_spec, message))

        if not handled:
            if message.get('req_type') not in ('pong', ):
                logging.debug('Unhandled message: %s' % message)

    def _ws_subscribe(self, event, call_spec):
        hooks = self._ws_callbacks.get(event, [])
        hooks.append(call_spec)
        self._ws_callbacks[event] = hooks

    def unsubscribe(self, name):
        """
        Remove all callbacks labeled with the given name.
        """
        for event, hooks in self._ws_callbacks.items():
            rm = [i for i in range(0, len(hooks)) if (hooks[i][0] == name)]
            for i in reversed(rm):
                hooks.pop(i)

        # Opportunistically clean up any failed events as well
        before = time.time() - 3600
        for exp, req_id in self._ws_failed:
            if exp < before:
                self._ws_callbacks.pop(req_id, None)

    def on_error(self, on_error, name=None):
        """
        Subscribe to error events.

        The callback will be invoked with this moggie as its first argument,
        the message object as as its second argument, and if an exception
        was thrown processing, the exception will be the third.
        """
        self._ws_subscribe('*', (name, None, on_error))

    def on_notification(self, callback, on_error=None, name=None):
        """
        Subscribe to notification events.

        The callback will be invoked with this moggie as its first argument,
        and the message object as as its second argument.
        """
        self._ws_subscribe('notification', (name, '_RAW_', callback, on_error))

    def on_result(self, command, callback, on_error=None, raw=False, name=None):
        """
        Subscribe to any results for a given command.

        The callback will be invoked with this moggie as its first argument,
        and he message object as as its second argument.

        Setting `command='*'` will subscribe to all incoming events.
        Setting `raw=True` will return full JSON messages as received.
        """
        req_type = ('cli:%s' % command) if (command != '*') else '*'
        self._ws_subscribe(req_type,
            (name, '_RAW_' if raw else '', callback, on_error))

    def websocket_send(self, message,
            on_success=None, on_error=None, name=None):
        """
        Send a message directly to the websocket.

        If the message has a `req_id` field and `on_success` or `on_error`
        are specified, a callback will be registered.
        """
        req_id = message.get('req_id')
        if (on_success or on_error) and req_id:
            if req_id in self._ws_callbacks:
                self._ws_callbacks[req_id].append((name, on_success, on_error))
            else:
                self._ws_callbacks[req_id] = [(name, on_success, on_error)]
        self._bridge.send(to_json(message))

    def _ws_command(self, command, args, on_success, on_error):
        self.websocket_send(RequestCommand(command, args),
            on_success=on_success, on_error=on_error)

    def _pre_start(self):
        if self._app or self._app_worker:
            raise Nonsense('Already connected to a running moggie')

    def _on_start(self, result):
        self.connect()

    def _on_stop(self, result):
        self._app = self._app_worker = None

    def _tell_user(self, msg):
        if self._mode == self.MODE_CLI:
            sys.stderr.write(msg.rstrip() + '\n')
            sys.stderr.flush()
        else:
            # FIXME: There might be better ways...
            logging.info(msg)

    def enable_default_logging(self):
        import logging
        from .config.paths import configure_logging
        self.DEFAULT_LOG_LEVEL = int(self._config.get(
            self._config.GENERAL, 'log_level', fallback=logging.DEBUG))
        configure_logging(
            profile_dir=self.work_dir,
            level=self.DEFAULT_LOG_LEVEL)
        return self

    def connect(self, *args, autostart=True):
        if self._app:
            return self

        if not self._app_worker:
            from .workers.app import AppWorker
            self._app_worker = AppWorker.FromArgs(self.work_dir, args)

        if self._app_worker:
            if not self._app_worker.connect(autostart=autostart, quick=True):
                raise NotRunning('Failed to launch or connect to app')
            logging.debug('Connected %s' % self._app_worker)

        if not self._app:
            self._app = self._app_worker.get_app()

        return self

    def set_output(self, destination=None, eof_marker=None):
        """
        Configure where to send command output.

        The destination must be either None, or an object with a `write()`
        method (such as a file descriptor) or an `append()` method.

        This allows long-running operations to stream their results as they
        become available (e.g. under asyncio). Note that reusing an output
        buffer for multiple commands in parallel will result in predictably
        mixed output.

        Upon completion, each command will write an eof_marker to the
        destination.

        Setting the destination to None will allocate a new buffer (a list)
        for each command.
        """
        self._output = destination
        self._output_eof = eof_marker
        return self

    def set_input(self, source=None):
        """
        Configure where to read command input (stdin) from.

        The source must be either None, or an object with a `read()`
        method (such as a file descriptor), or a `pop(0)` method.

        Setting the destination to None will provide no "standard input"
        to running commands.
        """
        self._input = source
        return self

    def set_mode(self, mode):
        self._mode = mode
        self.set_input(sys.stdin.buffer if (mode == self.MODE_CLI) else None)
        self.set_output(sys.stdout if (mode == self.MODE_CLI) else None)
        return self

    def set_access(self, access):
        self._access = access
        return self

    def run(self, command, *args, **kwargs):
        """
        Run the named moggie command synchronously, returning a list of
        zero or more results. In CLI mode, results are always empty, as
        any output gets written to stdout.

        Note if this is run from within an active event loop, the returned
        list will always be empty - it will be populated with results later
        on, but due to the sync/async callstacks not cooperating nicely
        we cannot easily predict when.
        """
        result = []
        kwargs['result_buffer'] = result

        command_impl = self._COMMANDS.get(command)
        if not self._can_run_async(command_impl):
            if command in self._PRE_HOOKS:
                self._PRE_HOOKS[command](self)
            result.append(command_impl(self, list(args)))
            if command in self._HOOKS:
                self._HOOKS[command](self, result)
            return result

        # Update our idea of what the current event loop is
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        task = self.async_run(command, *args, **kwargs)
        if self.loop.is_running():
            self._async_tasks.append(self.loop.create_task(task))
            return result
        else:
            task = asyncio.ensure_future(task)
            while not task.done():
                try:
                    return self.loop.run_until_complete(task)
                except KeyboardInterrupt:
                    if task:
                        task.cancel()

    async def async_run(self, command, *args, **kwargs):
        """
        Run the named moggie command asynchronously.
        """
        command_obj = self._COMMANDS.get(command)
        if not self._can_run_async(command_obj):
            raise RuntimeError('Cannot run async: %s' % command)

        if command in self._PRE_HOOKS:
            self._PRE_HOOKS[command](self)

        result = kwargs.pop('result_buffer', [])
        moggie_wrap = kwargs.pop('moggie_wrap', lambda m: m)
        on_success = kwargs.pop('on_success', False)
        on_error = kwargs.pop('on_error', None)
        have_callbacks = (on_success is not False)
        args = list(args)

        def finish(moggie, res):
            nonlocal command, result, have_callbacks
            if command in self._HOOKS:
                self._HOOKS[command](self, res)

            res = None if (res and res[0] is None) else res
            if have_callbacks:
                # Implement the callback API in an effectively synchronous way
                # in the cases where we are running without a live websocket.
                on_success(moggie_wrap(moggie), res)
            else:
                return res

        if self._mode == self.MODE_CLI:
            if command_obj is None:
                await self.async_run('help')
                result.append(False)
            elif hasattr(command_obj, 'Command'):
                command_obj = command_obj(self.work_dir, list(args),
                    access=self._access)
                await command_obj.async_ready()
                result.append(await command_obj.run())
            else:
                result.append(command_obj(self, list(args)))

        elif self._mode == self.MODE_PYTHON:
            if not hasattr(command_obj, 'MsgRunnable'):
                raise Nonsense('Not usable from within Python, sorry')

            if have_callbacks and self._bridge:
                self._ws_command(command, args, finish, on_error)
                return None
            else:
                rbuf_cmd = await command_obj.MsgRunnable(self, args)
                if rbuf_cmd is None:
                    raise Nonsense('Not usable from within Python, sorry')
                result, obj = rbuf_cmd
                await obj.run()

        return finish(self, result)

class MoggieCLI(Moggie):
    """
    A Moggie object configured for use as a command-line tool by default,
    reading and writing from standard input/output.
    """
    DEFAULT_MODE = 'cli'


class MoggieAsync(Moggie):
    """
    A Moggie object configured to use async methods by default when possible.
    """
    PREFER_ASYNC = True


class MoggieContext:
    """
    A class representing a specific context within a running Moggie.

    If a `context_id` is provided without any `info`, the Moggie will
    be queried for the context's current settings (which are accessible
    as properties of the object).

    Method calls are proxied to the underlying Moggie object, with
    the `--context=X` argument set appropriately. An example:

        mog_ctx = MoggieContext(moggie, context='Context 1')

        # These are equivalent:
        moggie.search('bjarni', context='Context 1')
        mog_ctx.search('bjarni')

    Any `on_success` callbacks (in message passing mode) will be invoked
    with the MoggieContext as their first argument, instead of the
    underlying Moggie.
    """
    def __init__(self, moggie, context_id=None, info=None):
        self.moggie = moggie
        self._info = {
            'key': context_id or '',
            'name': context_id or '',
            'description': '',
            'tags': [],
            'ui_tags': [],
            'accounts': {},
            'identities': {}}
        if info is None:
            if context_id is None:
                context_id = 'Context 0'  # FIXME: Should load from Config
            self._ctx_id = context_id
            self._info.update(self.context(output='details')[0][context_id])
        else:
            self._ctx_id = info['key']
            self._info.update(info)
        self.update = self._info.update
        self.get = self._info.get

    key = property(lambda s: s._info['key'])
    name = property(lambda s: s._info['name'])
    tags = property(lambda s: s._info['tags'])
    default_ui_tags = property(lambda s: s._info.get('default_ui_tags', False))
    ui_tags = property(lambda s: s._info['ui_tags'])
    accounts = property(lambda s: s._info['accounts'])
    identities = property(lambda s: s._info['identities'])
    description = property(lambda s: s._info['description'])

    def __getattribute__(self, attr):
        try:
            return super().__getattribute__(attr)
        except AttributeError:
            pass

        method = self.moggie.__getattribute__(attr)
        if callable(method):
            return lambda *a, **kwa: method(*a,
                context=self._ctx_id,
                moggie_wrap=lambda m: self,  # Replace Moggie w/ MoggieContext
                **kwa)

        raise AttributeError(attr)


Moggie.Setup()
MoggieCLI.Setup()
MoggieAsync.Setup()
