import asyncio
import json
import logging
import socket
import time
import traceback
import websockets

from .dumbcode import to_json, from_json
from .http import http1x_connect
from ..api.requests import RequestPing


DEBUG_TRACE = False


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
        upload = to_json(kwargs).encode('latin-1')
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
                return from_json(cfd.read())
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
    PING_INTERVAL = 30

    def __init__(self, ev_loop, name, app, peer):
        self.ws = None
        self.ws_url = app.websocket_url()
        self.ws_headers = {'Authorization': 'Bearer %s' % app.auth_token}
        self.name = name
        self.app = app
        self.message_sink = peer.link_bridge(self)
        self.ev_loop = ev_loop
        self.keep_running = True
        self.pending = []
        self.broken = 0

    def __str__(self):
        return '%s(%s, %s)' % (type(self).__name__, self.name, self.ws_url)

    def send(self, message):
        self.pending.append(message)

    def send_json(self, data):
        self.pending.append(to_json(data))

    async def on_close(self, exc):
        # FIXME: OSError probably means our backend went away and is
        #        not coming back. How should we handle that?
        #        The app needs to know, and inform the user, with
        #        the option to relaunch if this is a local backend.
        self.broken += 1
        self.message_sink(self.name, to_json({
            'internal_websocket_error': 'Websocket connection unusable',
            'count': self.broken}))
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def async_send(self, message):
        try:
            await self.ws.send(message)
            self.broken = 0
        except Exception as e:
            self.pending.append(message)
            await self.on_close(e)

    async def queue_flusher(self):
        while self.keep_running:
            if self.ws is not None:
                try:
                    if self.pending:
                        for message in self.pending:
                            await self.ws.send(message)
                        if DEBUG_TRACE:
                            logging.debug('%s: Sent %d messages'
                                % (self, len(self.pending)))
                        self.pending = []
                    else:
                        await self.ws.send(to_json(RequestPing()))
                except (OSError, websockets.exceptions.ConnectionClosed) as e:
                    await self.on_close(e)
                except exception as e:
                    logging.exception('%s: Flushing queue failed' % self)
                    await self.on_close(e)
            for i in range(0, 10 * self.PING_INTERVAL):
                await asyncio.sleep(0.1)
                if self.pending and self.ws:
                    break

    async def run(self):
        self.ev_loop.create_task(self.queue_flusher())
        reconn_delay = 0
        while self.keep_running:
            try:
                logging.debug('%s: Connecting' % self)

                async with websockets.connect(self.ws_url,
                        origin=self.ws_url,
                        compression=None,
                        timeout=60,
                        ping_timeout=5,
                        ping_interval=self.PING_INTERVAL,
                        close_timeout=1,
                        max_size=50*1024*1024,
                        max_queue=2,
                        extra_headers=self.ws_headers) as ws:
                    self.ws = ws
                    self.broken = 0
                    reconn_delay = 0
                    async for message in ws:
                        self.message_sink(self.name, message)
                        if DEBUG_TRACE:
                            logging.debug('%s: Message received' % self)
            except (OSError, websockets.exceptions.ConnectionClosed) as e:
                await self.on_close(e)
            except Exception as e:
                logging.exception('%s: error' % self)
                await self.on_close(e)

            reconn_delay += 1
            logging.warning('%s: Websocket closed' % self)
            await asyncio.sleep(min(reconn_delay, 15))


def AsyncRPCBridge(ev_loop, name, app, peer):
    app_bridge = BridgeWorker(ev_loop, name, app, peer)
    ev_loop.create_task(app_bridge.run())
    return app_bridge


if __name__ == '__main__':
    pass
