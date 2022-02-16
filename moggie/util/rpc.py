import asyncio
import json
import socket
import time
import traceback
import websockets

from ..util.http import http1x_connect
from ..jmap.requests import RequestPing


class JsonRpcClient:
    PEEK_BYTES = 4096

    def __init__(self, base_url, secret=None):
        self.url = base_url
        try:
            proto, _, hostport, url_secret = self.url.split('/', 3)
            url_secret = url_secret.rstrip('/')
        except ValueError:
            proto, _, hostport = self.url.split('/', 2)
            url_secret = None

        try:
            self.host, self.port = hostport.split(':', 1)
        except ValueError:
            self.host = hostport
            self.port = 80

        if secret or url_secret:
            self.path_prefix = '/%s/' % (secret or url_secret)
        else:
            self.path_prefix = '/'

        if proto.startswith('https'):
            assert(not 'implemented')
            self.call = self.https_call
            self.async_call = self.async_https_call
        else:
            self.call = self.http_call
            self.async_call = self.async_http_call

    def http_call(self, method, **kwargs):
        upload = json.dumps(kwargs).encode('latin-1')
        conn = http1x_connect(self.host, self.port, self.path_prefix + method,
            method='POST',
            headers=(
                'Content-Type: application/json\r\nContent-Length: %d\r\n'
                % len(upload)),
            more=True)
        for i in range(0, len(upload), 4096):
            conn.send(upload[i:i+4096])
        conn.shutdown(socket.SHUT_WR)

        peeked = conn.recv(self.PEEK_BYTES, socket.MSG_PEEK)
        if b' 200 ' in peeked[:13]:
            hdr = peeked.split(b'\r\n\r\n', 1)[0]
            junk = conn.recv(len(hdr) + 4)
            with conn.makefile(mode='rb') as cfd:
                return json.load(cfd)
        else:
            # FIXME: Parse the HTTP response code and raise better exceptions
            raise PermissionError(str(peeked[:12], 'latin-1'))

    async def async_http_call(self, method, **kwargs):
        return self.http_call(method, **kwargs)  # FIXME

    def https_call(self, method, **kwargs):
        assert(not 'implemented')

    async def async_https_call(self, method, **kwargs):
        return self.https_call(method, **kwargs)  # FIXME


class BridgeWorker:
    PING_INTERVAL = 3

    def __init__(self, ev_loop, app, peer):
        self.ws = None
        self.ws_url = app.websocket_url()
        self.ws_headers = {'Authorization': 'Bearer %s' % app.auth_token}
        self.app = app
        self.message_sink = peer.link_bridge(self)
        self.ev_loop = ev_loop
        self.keep_running = True
        self.pending = []

    def send(self, message):
        self.pending.append(message)

    def send_json(self, data):
        self.pending.append(json.dumps(data))

    async def async_send(self, message):
        await self.ws.send(message)

    async def queue_flusher(self):
        while self.keep_running:
            if self.ws is not None:
                if self.pending:
                    for message in self.pending:
                        await self.ws.send(message)
                    self.pending = []
                else:
                    await self.ws.send(json.dumps(RequestPing()))
            for i in range(0, 10 * self.PING_INTERVAL):
                await asyncio.sleep(0.1)
                if self.pending:
                    break

    async def run(self):
        self.ev_loop.create_task(self.queue_flusher())
        while self.keep_running:
            try:
                async with websockets.connect(self.ws_url,
                        origin=self.ws_url,
                        compression=None,
                        timeout=60,
                        max_size=50*1024*1024,
                        max_queue=2,
                        extra_headers=self.ws_headers) as ws:
                    self.ws = ws
                    async for message in ws:
                        self.message_sink(message)
            except (OSError, websockets.exceptions.ConnectionClosed):
                self.ws = None
                # FIXME: OSError probably means our backend went away and is
                #        not coming back. How should we handle that?
                #        The app needs to know, and inform the user, with
                #        the option to relaunch if this is a local backend.
                print('FIXME: Dangit, websocket connection closed')
                await asyncio.sleep(1)
            except:
                self.ws = None
                traceback.print_exc()


def AsyncRPCBridge(ev_loop, app, peer):
    app_bridge = BridgeWorker(ev_loop, app, peer)
    ev_loop.create_task(app_bridge.run())
    return app_bridge


if __name__ == '__main__':
    pass
