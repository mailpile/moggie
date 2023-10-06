import asyncio
import sys


class Moggie:
    """
    This is the public Moggie API, in its Pythonic form.

    An instance of this class (usually) represents an authenticated session
    with a running moggie, although some methods (including `email` and
    `parse`) may be run without a backend. The moggie backend may be remote
    or local, or internal to the running Python environment.

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

    API methods will simply return their output buffer (for convenience)
    on success, raising exceptions for certain errors.

    NOTE: the synchronous methods use the async code behind the scenes,
          but cannot await completion if there is already an event loop
    running. This means the output buffer may get populated at an
    unexpected time, well after the synchronious method has returned. To
    avoid confusion using async methods is preferred.

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

    def connect(self, url=None, autostart=False):
        return self

    def __init__(self,
            work_dir=None,
            app_worker=None,
            access=None,
            mode=None):

        from .config.paths import AppConfig, DEFAULT_WORKDIR

        if work_dir and app_worker:
            raise Nonsense('Specify work_dir or app_worker, not both')
        elif work_dir is None:
            work_dir = DEFAULT_WORKDIR()

        if app_worker:
            self.work_dir = app_worker.profile_dir
            self._app_worker = app_worker
            self._config = app_worker.config
        else:
            self.work_dir = work_dir
            self._app_worker = None
            self._config = AppConfig(work_dir)
            self._access = None

        self.set_input()
        self.set_access(access)
        self.set_mode(mode or self.DEFAULT_MODE)

        # Merge the app COMMANDS with the CLI_COMMANDS registry.
        # Trust me, I know what I'm doing!
        from . import app
        from .app.cli import CLI_COMMANDS
        CLI_COMMANDS.update({
            'default': app.CommandMuttalike,
            'restart': app.CommandRestart,
            'start': app.CommandStart,
            'stop': app.CommandStop,
            'tui': app.CommandTUI})
        app.COMMANDS = CLI_COMMANDS
        self._commands = CLI_COMMANDS

        def _kwas_to_args(kwa):
            for k, v in kwa.items():
                if k[:1] == '_':
                    k = k[1:]
                if isinstance(v, bool):
                    if v:
                        yield '--%s' % k
                else:
                    yield '--%s=%s' % (k, v)

        def _fix_args(a, kwa):
            args = list(a)
            args.extend(_kwas_to_args(kwa))
            return args

        def _mk_method(cmd):
            return lambda *a, **kwa: self.run(cmd, *_fix_args(a, kwa))

        def _mk_async_method(cmd):
            async def _async_runner(*a, **kwa):
                return await self.async_run(cmd, *_fix_args(a, kwa))
            return _async_runner

        self._async_tasks =  []
        for command in self._commands:
            name = command.replace('-', '_')
            setattr(self, name, _mk_method(command))
            if self._can_run_async(self._commands.get(command)):
                setattr(self, 'async_' + name, _mk_async_method(command))

    def _can_run_async(self, command):
        return not (command and not hasattr(command, 'Command'))

    def enable_default_logging(self):
        import logging
        from .config.paths import configure_logging
        self.DEFAULT_LOG_LEVEL = int(self._config.get(
            self._config.GENERAL, 'log_level', fallback=logging.DEBUG))
        configure_logging(
            profile_dir=self.work_dir,
            level=self.DEFAULT_LOG_LEVEL)
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
        self.set_input(sys.stdin if (mode == self.MODE_CLI) else None)
        self.set_output(sys.stdout if (mode == self.MODE_CLI) else None)
        return self

    def set_access(self, access):
        self._access = access
        return self

    def connect(self):
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
        command_impl = self._commands.get(command)
        if not self._can_run_async(command_impl):
            result.append(command_impl(self, list(args)))
            return result

        kwargs['result_buffer'] = result
        loop = asyncio.get_event_loop()
        task = self.async_run(command, *args, **kwargs)
        if loop.is_running():
            self._async_tasks.append(loop.create_task(task))
            return result
        else:
            task = asyncio.ensure_future(task)
            while not task.done():
                try:
                    return loop.run_until_complete(task)
                except KeyboardInterrupt:
                    if task:
                        task.cancel()

    async def async_run(self, command, *args, **kwargs):
        """
        Run the named moggie command asynchronously.
        """
        command = self._commands.get(command)
        if not self._can_run_async(command):
            raise RuntimeError('Cannot run async: %s' % command)

        if 'result_buffer' in kwargs:
            result = kwargs.pop('result_buffer')
        else:
            result = []

        if self._mode == self.MODE_CLI:
            if command is None:
                await self.async_run('help')
                result.append(False)
            elif hasattr(command, 'Command'):
                command_obj = command(self.work_dir, list(args), access=True)
                await command_obj.async_ready()
                result.append(await command_obj.run())
            else:
                result.append(command(self, list(args)))

        elif self._mode == self.MODE_PYTHON:
            if hasattr(command, 'MsgRunnable'):
                result, obj = command.MsgRunnable(self.app, None, args)
                await obj()
            else:
                raise Nonsense('Not usable from within Python, sorry')

        return None if (result and result[0] is None) else result


class MoggieCLI(Moggie):
    """
    A Moggie object configured for use as a command-line tool by default,
    reading and writing from standard input/output.
    """
    DEFAULT_MODE = 'cli'
