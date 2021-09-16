import asyncio
import json
import socket
import traceback
import websockets

from ..util.http import http1x_connect


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
        for i in range(0, len(upload)//4096 + 1):
            conn.send(upload[i*4096:(i+1)*4096])
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
    def __init__(self, ev_loop, app, peer):
        self.ws = None
        self.ws_url = app.websocket_url()
        self.app = app
        self.message_sink = peer.link_bridge(self)
        self.ev_loop = ev_loop
        self.keep_running = True

    def send(self, message):
        async def post_message():
            await self.ws.send(message)
        self.ev_loop.create_task(post_message)

    async def run(self):
        while self.keep_running:
            try:
                async with websockets.connect(self.ws_url, origin=self.ws_url) as ws:
                    self.ws = ws
                    async for message in ws:
                        self.message_sink(message)
            except (OSError, websockets.exceptions.ConnectionClosed):
                # FIXME: OSError probably means our backend went away and is
                #        not coming back. How should we handle that?
                #        The app needs to know, and inform the user, with
                #        the option to relaunch if this is a local backend.
                await asyncio.sleep(1)
            except:
                traceback.print_exc()


def AsyncRPCBridge(ev_loop, app, peer):
    app_bridge = BridgeWorker(ev_loop, app, peer)
    ev_loop.create_task(app_bridge.run())
    return app_bridge


if __name__ == '__main__':
    pass
