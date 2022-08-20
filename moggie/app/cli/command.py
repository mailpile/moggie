import asyncio
import json
import time


class Nonsense(Exception):
    pass


class CLICommand:
    AUTO_START = True
    HELP = ''

    @classmethod
    def Command(cls, wd, args):
        return cls(wd, args).sync_run()

    def __init__(self, wd, args):
        from ...workers.app import AppWorker
        from ...util.rpc import AsyncRPCBridge

        self.worker = AppWorker.FromArgs(wd, self.configure(args))
        if not self.worker.connect(autostart=self.AUTO_START):
            raise Nonsense('Failed to launch or connect to app')

        self.connected = False
        self.messages = []
        self.ev_loop = asyncio.get_event_loop()
        self.app = AsyncRPCBridge(self.ev_loop, 'cli', self.worker, self)
        self.ev_loop.run_until_complete(self._await_connection())

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

    def configure(self, args):
        return args

    async def await_messages(self, *prototypes, timeout=10):
        sleeptime, deadline = 0, (time.time() + timeout)
        while time.time() < deadline:
            sleeptime = min(sleeptime + 0.01, 0.1)
            await asyncio.sleep(sleeptime)
            while self.messages:
                msg = self.messages.pop(0)
                if msg.get('prototype') in prototypes:
                    return msg
        return {}

    async def run(self):
        raise Nonsense('Unimplemented')

    def sync_run(self):
        self.ev_loop.run_until_complete(self.run())
