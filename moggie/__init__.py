import asyncio
import logging
import sys

from .app.cli.exceptions import NotRunning, Nonsense


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

    Moggie's API methods can be invoked either synchronously, or async.
    The async methods have a `async_` prefix to the method names.

    API methods will simply return their output buffer on success,
    raising exceptions for certain errors.

    NOTE: the synchronous methods use the async code behind the
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
            for k, v in kwa.items():
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
            args = list(a)
            args.extend(_kwas_to_args(kwa))
            return args

        def _mk_method(cmd):
            return lambda s, *a, **kwa: s.run(cmd, *_fix_args(a, kwa))

        def _mk_async_method(cmd):
            async def _async_runner(self, *a, **kwa):
                return await self.async_run(cmd, *_fix_args(a, kwa))
            return _async_runner

        for command in cls._COMMANDS:
            name = command.replace('-', '_')

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
            mode=None):

        from .config.paths import AppConfig, DEFAULT_WORKDIR

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
            self._access = None

        self._async_tasks =  []

        self.loop = asyncio.get_event_loop()
        self.set_input()
        self.set_access(access)
        self.set_mode(mode or self.DEFAULT_MODE)

    def _pre_start(self):
        if self._app or self._app_worker:
            raise Nonsense('Already connected to a running moggie')

    def _on_start(self, result):
        self.connect()

    def _on_stop(self, result):
        self._app = self._app_worker = None

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
        command_impl = self._COMMANDS.get(command)
        if not self._can_run_async(command_impl):
            if command in self._PRE_HOOKS:
                self._PRE_HOOKS[command](self)
            result.append(command_impl(self, list(args)))
            if command in self._HOOKS:
                self._HOOKS[command](self, result)
            return result

        # Update our idea of what the current event loop is
        self.loop = asyncio.get_event_loop()

        kwargs['result_buffer'] = result
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

        if 'result_buffer' in kwargs:
            result = kwargs.pop('result_buffer')
        else:
            result = []

        args = list(args)

        if self._mode == self.MODE_CLI:
            if command_obj is None:
                await self.async_run('help')
                result.append(False)
            elif hasattr(command_obj, 'Command'):
                command_obj = command_obj(self.work_dir, list(args), access=True)
                await command_obj.async_ready()
                result.append(await command_obj.run())
            else:
                result.append(command_obj(self, list(args)))

        elif self._mode == self.MODE_PYTHON:
            if hasattr(command_obj, 'MsgRunnable'):
                rbuf_cmd = await command_obj.MsgRunnable(self, args)
                if rbuf_cmd is None:
                    raise Nonsense('Not usable from within Python, sorry')
                result, obj = rbuf_cmd
                await obj.run()
            else:
                raise Nonsense('Not usable from within Python, sorry')

        if command in self._HOOKS:
            self._HOOKS[command](self, result)

        return None if (result and result[0] is None) else result


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


Moggie.Setup()
MoggieCLI.Setup()
MoggieAsync.Setup()
