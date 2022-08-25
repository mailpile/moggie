import asyncio
import copy
import json
import logging
import time
import sys


class NotRunning(Exception):
    pass


class Nonsense(Exception):
    pass


class CLICommand:
    AUTO_START = False
    NAME = 'command'
    OPTIONS = {}
    CONNECT = True
    WEBSOCKET = True

    @classmethod
    def Command(cls, wd, args):
        try:
            return cls(wd, args).sync_run()
        except BrokenPipeError:
            return False
        except Nonsense as e:
            sys.stderr.write('%s failed: %s\n' % (cls.NAME, e))
            return False
        except Exception as e:
            logging.exception('%s failed' % cls.NAME)
            sys.stderr.write('%s failed: %s\n' % (cls.NAME, e))
            return False

    @classmethod
    async def WebRunnable(cls, app, frame, conn, args):
        def reply(msg, eof=False):
            conn.sync_reply(frame, bytes(msg, 'utf-8'), eof=eof)
        try:
            cmd_obj = cls(app.profile_dir, args, appworker=app, connect=False)
            cmd_obj.write_reply = reply
            cmd_obj.write_error = reply
            return cmd_obj
        except:
            logging.exception('Failed %s' % cls.NAME)
            reply('', eof=True)

    def __init__(self, wd, args, appworker=None, connect=True):
        from ...workers.app import AppWorker
        from ...util.rpc import AsyncRPCBridge

        self.options = copy.deepcopy(self.OPTIONS)
        self.connected = False
        self.messages = []
        self.workdir = wd

        self.mimetype = 'text/plain; charset=utf-8'
        self.write_reply = sys.stdout.write
        self.write_error = sys.stderr.write

        if connect and self.CONNECT:
            self.worker = AppWorker.FromArgs(wd, self.configure(args))
            if not self.worker.connect(autostart=self.AUTO_START, quick=True):
                raise NotRunning('Failed to launch or connect to app')
        else:
            self.configure(args)
            self.worker = appworker

        self.ev_loop = asyncio.get_event_loop()
        if connect and self.WEBSOCKET:
            self.app = AsyncRPCBridge(self.ev_loop, 'cli', self.worker, self)
            self.ev_loop.run_until_complete(self._await_connection())

    def print(self, *args):
        self.write_reply(' '.join(args) + '\n')

    def error(self, *args):
        self.write_error(' '.join(args) + '\n')

    async def _await_connection(self):
        sleeptime, deadline = 0, (time.time() + 10)
        while time.time() < deadline:
            sleeptime = min(sleeptime + 0.01, 0.1)
            await asyncio.sleep(sleeptime)
            if self.connected:
                break

    def link_bridge(self, bridge):
        def _receive_message(bridge_name, raw_message):
            message = json.loads(raw_message)
            if message.get('connected'):
                self.connected = True
            else:
                self.handle_message(message)
        return _receive_message

    def handle_message(self, message):
        self.messages.append(message)

    def context(self):
        from ...config import AppConfig
        cfg = AppConfig(self.workdir)
        ctx = (self.options.get('--context=') or ['default'])[-1]
        if ctx == 'default':
            ctx = cfg.get(
                AppConfig.GENERAL, 'default_cli_context', fallback='Context 0')
        else:
            pass # FIXME: Allow the user to select context by name, not
                 #        only the unfriendly "Context N" key.
        return ctx

    def metadata_worker(self):
        from ...workers.metadata import MetadataWorker
        return MetadataWorker.Connect(self.worker.worker_dir)

    def search_worker(self):
        from ...workers.search import SearchWorker
        return SearchWorker.Connect(self.worker.worker_dir)

    def strip_options(self, args):
        # This should be compatible-ish with how notmuch does things.
        leftovers = []
        def _setopt(name, val):
            if name not in self.options:
                self.options[name] = []
            self.options[name].append(val)
        while args:
            arg = args.pop(0)
            if arg == '--':
                leftovers.extend(args)
                break
            elif arg+'=' in self.OPTIONS:
                _setopt(arg+'=', args.pop(0))
            elif arg in self.OPTIONS:
                _setopt(arg, True)
            elif arg[:2] == '--':
                if '=' in arg:
                    arg, opt = arg.split('=', 1)
                else:
                    arg, opt = arg.split(':', 1)
                if arg+'=' not in self.OPTIONS:
                    raise Nonsense('Unrecognized argument: %s' % arg)
                _setopt(arg+'=', opt)
            else:
                leftovers.append(arg)
        return leftovers

    def configure(self, args):
        return args

    async def await_messages(self, *prototypes, timeout=10):
        sleeptime, deadline = 0, (time.time() + timeout)
        while time.time() < deadline:
            if not self.messages:
                sleeptime = min(sleeptime + 0.01, 0.1)
                await asyncio.sleep(sleeptime)
            while self.messages:
                msg = self.messages.pop(0)
                if msg.get('prototype') in prototypes:
                    return msg
        return {}

    async def run(self):
        raise Nonsense('Unimplemented')

    async def web_run(self):
        try:
            return await self.run()
        except:
            logging.exception('Failed to run %s' % self.NAME)
        finally:
            try:
                self.write_reply('', eof=True)
            except:
                pass

    def sync_run(self):
        task = asyncio.ensure_future(self.run())
        while not task.done():
            try:
                self.ev_loop.run_until_complete(task)
            except KeyboardInterrupt:
                if task:
                    task.cancel()
