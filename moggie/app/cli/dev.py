import asyncio
import json
import logging
import shlex
import sys

from ...api.requests import *
from ...config import AppConfig, AccessConfig
from ...util.asyncio import async_run_in_thread
from ...util.rpc import AsyncRPCBridge
from ...util.dumbcode import to_json, from_json
from .command import Nonsense, CLICommand

try:
    import readline
except ImportError:
    readline = None


class CommandWebsocket(CLICommand):
    """moggie websocket [<URL>]

    This establishes a websocket connection to a running moggie server,
    sending input received over STDIN and echoing back any messages from
    the server.

    URLs for connecting as different users/roles can be obtained using:

        moggie grant --output=urls

    If no URL is specified, the tool connects to the user's default local
    moggie (with unlimited access).

    ### Options

    %(OPTIONS)s

    """
    NAME = 'websocket'
    ROLES = AccessConfig.GRANT_ACCESS  # FIXME: Allow user to see own contexts?
    WEBSOCKET = False
    AUTO_START = False
    WEB_EXPOSE = False
    OPTIONS = [[
        ('--friendly',   [False], 'Enable the user friendly input mode'),
        ('--exit-after=', [None], 'X=maximum number of received messages')]]

    def __init__(self, *args, **kwargs):
        self.ws_url = None
        self.ws_tls = False
        self.ws_hostport = None
        self.ws_auth_token = None
        self.received = 0
        super().__init__(*args, **kwargs)

    def configure(self, args):
        args = self.strip_options(args)
        if len(args) > 1:
            raise Nonsense('Too many arguments')

        url = args[0] if (len(args) > 0) else None
        try:
            if url:
                proto, _, hostport, token = url.split('/', 3)
                if token[-1:] == '/':
                    token = token[:-1]
                if token[:1] == '@':
                    token = token[1:]
                if proto not in ('http:', 'https:'):
                    raise ValueError(proto)
                if not len(token) > 5:
                    raise ValueError(token)
                self.ws_tls = (proto == 'https:')
                self.ws_hostport = hostport
                self.ws_auth_token = token
                self.ws_url = 'ws%s://%s/ws' % (
                    's' if self.ws_tls else '', self.ws_hostport)
        except ValueError:
            import traceback
            traceback.print_exc()
            raise Nonsense('Invalid URL: %s' % url)

        return []

    def link_bridge(self, bridge):
        return self.handle_message

    def print_message(self, message):
        if self.options['--friendly']:
            # Note: We don't use from_json() here, because we don't
            #       want to decode the binary data.
            print('<= ' + json.dumps(json.loads(message), indent=2))
        else:
            print('%s' % message)

    def handle_message(self, bridge_name, message):
        self.print_message(message)
        self.received += 1
        exit_after = self.options['--exit-after='][-1]
        if exit_after and self.received >= int(exit_after):
            sys.exit(1)

    async def read_json_loop(self, reader, bridge):
        pending = ''
        while True:
            data = await reader.read(1)
            if not data:
                break
            pending += str(data, 'utf-8')
            if '{' == pending[:1] and pending[-2:] == '}\n':
                try:
                    data = from_json(pending)
                    bridge.send(pending)
                except:
                    sys.stderr.write('Malformed input: %s\n'
                        % pending.replace('\n', ' '))
                    pending = ''

    async def read_friendly_loop(self, reader, bridge):
        pending = ''
        sys.stdout.write("""\
# Welcome to `moggie websockets` in friendly mode!
#
# Type your commands and they will be converted to JSON and sent. Examples:
#
#    count from:bre
#    search --limit=10 bjarni
#
# Press CTRL+D or type `quit` to exit.
#\n""")
        while True:
            if readline is None:
                data = await reader.read(1)
            else:
                await asyncio.sleep(0.1)
                prompt = '   ... ' if pending else 'moggie '
                data = await async_run_in_thread(input, prompt)
                if data:
                    data = bytes(data + '\n', 'utf-8')
                else:
                    sys.stderr.write('\n')
            if data is None or (data.strip() == b'quit'):
                break
            if not data:
                continue
            pending += str(data, 'utf-8')
            if pending.endswith('\n'):
                message = None
                stripped = pending.strip()
                if stripped[:1] == '{':
                    if stripped[-1:] == '}':
                        message = stripped
                else:
                    args = shlex.split(stripped)
                    if args and args[0] == 'moggie':
                        args.pop(0)
                    if args:
                        message = to_json({
                           'req_type': 'cli:%s' % args.pop(0),
                           'req_id': int(time.time()),
                           'args': args})
                if message:
                    sys.stdout.write('=> %s\n' % message)
                    bridge.send(message)
                    await asyncio.sleep(0.5)
                    pending = ''

    async def run(self):
        ev_loop = asyncio.get_event_loop()

        if self.ws_url and self.ws_auth_token:
            bridge = AsyncRPCBridge(ev_loop, 'cli_websocket', None, self,
                ws_url=self.ws_url,
                auth_token=self.ws_auth_token)
            if self.options['--friendly']:
                print('##[ %s ]##\n#' % self.ws_url)
        else:
            app = self.connect()
            bridge = AsyncRPCBridge(ev_loop, 'cli_websocket', app, self)
            if self.options['--friendly']:
                print('##[ local moggie ]##\n#')

        async def connect_stdin_stdout(loop):
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, sys.stdin)
            w_transport, w_protocol = await loop.connect_write_pipe(
                asyncio.streams.FlowControlMixin, sys.stdout)
            writer = asyncio.StreamWriter(
                w_transport, w_protocol, reader, loop)
            return reader, writer

        try:
            reader = writer = None
            if self.options['--friendly']:
                if readline is None:
                    reader, writer = await connect_stdin_stdout(ev_loop)
                await self.read_friendly_loop(reader, bridge)
            else:
                reader, writer = await connect_stdin_stdout(ev_loop)
                await self.read_json_loop(reader, bridge)

        except (KeyboardInterrupt, asyncio.exceptions.CancelledError):
            if readline is not None:
                sys.stderr.write('\n')


class CommandNotifications(CommandWebsocket):
    """moggie notifications [<URL>]

    This establishes a websocket connection to a running moggie server,
    repeating any notifications to STDOUT.

    URLs for connecting as different users/roles can be obtained using:

        moggie grant --output=urls

    If no URL is specified, the tool connects to the user's default local
    moggie (with unlimited access).

    ### Options

    %(OPTIONS)s

    """
    NAME = 'notifications'
    ROLES = AccessConfig.GRANT_ACCESS  # FIXME: Is this right?
    WEBSOCKET = False
    AUTO_START = False
    WEB_EXPOSE = False
    OPTIONS = [[
        ('--format=',   ['text'], 'X=(json|text*) Output text or JSON'),
        ('--friendly',        [], None),
        ('--exit-after=', [None], 'X=maximum number of received messages')]]

    def print_message(self, message):
        fmt = self.options['--format='][-1]
        if fmt == 'json':
            print(json.dumps(json.loads(message), indent=2))
        else:
            message = json.loads(message)
            if 'message' in message:
                print('%s' % message['message'])
            elif message.get('connected') or message.get('req_type') == 'pong':
                print('Connected!  Waiting for notifications... (CTRL+C quits)')

    async def read_json_loop(self, reader, bridge):
        while True:
            await asyncio.sleep(1)
