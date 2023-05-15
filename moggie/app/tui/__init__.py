import asyncio
import logging
import os
import re
import sys
import time
import urwid

from moggie.app import Nonsense

from ...api.requests import *
from ...config import APPNAME, APPVER, configure_logging
from ...util.dumbcode import to_json, from_json
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker

from .decorations import palette
from .tui_frame import TuiFrame


class TuiConnManager:
    # This class is in charge of routing and/or annotating messages
    # between the UI and the backends - because there may be more than
    # one!
    def __init__(self, aev_loop, local_app_worker):
        self.local_app_worker = local_app_worker
        self.asyncio_event_loop = aev_loop

        self.connecting = []
        self.seq = 0
        self.bridges = {}
        self.handlers = {
            '*:internal_websocket_error': {'_wse': self.handle_ws_error}}

        if local_app_worker:
            AsyncRPCBridge(aev_loop, 'local_app', local_app_worker, self)

    def connect(self, name, remote_url, first_message):
        pass

    def link_bridge(self, bridge):
        self.bridges[bridge.name] = bridge
        return self.handle_message

    def close(self):
        for bridge in self.bridges.values():
            # FIXME: This is probably not enough?
            bridge.keep_running = False

    def add_handler(self, name, bridge_name, message_type, callback):
        if isinstance(message_type, dict):
            message_type = message_type['req_type']
        bridge_name = bridge_name.split('/')[0]
        pattern = '%s:%s' % (bridge_name, message_type)
        handlers = self.handlers.get(pattern, {})
        _id = '%x.%s' % (self.seq, name)
        handlers[_id] = callback
        self.seq += 1
        self.handlers[pattern] = handlers
        return _id

    def del_handler(self, _id):
        for pattern in self.handlers:
            if _id in self.handlers[pattern]:
                del self.handlers[pattern][_id]

    def send(self, message, bridge_name=None):
        if self.connecting is not None:
            self.connecting.append((message, bridge_name))
            return

        if not bridge_name:
            targets = ['local_app']
        elif bridge_name == '*':
            targets = self.bridges
        else:
            targets = [bridge_name]

        message = to_json(message)
        logging.debug('%s <= %s' % (targets, message))
        for target in targets:
            self.bridges[target].send(message)

    def flush_pending(self):
        pending, self.connecting = self.connecting, None
        for message, bridge_name in pending:
            self.send(message, bridge_name)

    def handle_message(self, bridge_name, message):
        logging.debug('Incoming(%s): %.512s' % (bridge_name, message))
        #logging.debug('Handlers: %s' % self.handlers)
        try:
            message = from_json(message)
        except:
            logging.exception('Failed to parse message from %s: %s'
                % (bridge_name, message))
            return

        message_type = message.get('req_type')
        if not message_type:
            if message.get('connected'):
                self.flush_pending()
                message_type = 'connected'
            elif 'internal_websocket_error' in message:
                message_type = 'internal_websocket_error'
            else:
                message_type = 'unknown'

        for pattern in (
                '%s:%s' % (bridge_name, message_type),
                '*:%s' % message_type,
                '%s:*' % bridge_name,
                '*:*'):
            failed = set()
            for tid, target in self.handlers.get(pattern, {}).items():
                try:
                    target(bridge_name, message)
                except:
                    logging.exception(
                        'Choked on message from %s, disabling handler %s'
                        % (bridge_name, tid))
                    failed.add(tid)
            for tid in failed:
                del self.handlers[pattern][tid]

    def handle_ws_error(self, bridge_name, message):
        logging.debug('FIXME: Tear down the connection and make a new one?')


def Main(workdir, tui_args, send_draft):
    for arg in ('-E', '-p', '-R', '-y', '-Z'):
        if arg in tui_args:
            raise Nonsense('FIXME: Unimplemented: moggie %s' % (arg,))

    logging.info('Starting %s v%s text UI with pid=%d'
        % (APPNAME, APPVER, os.getpid()))
    logging.debug('.. tui_args = %s' % (tui_args))
    logging.debug('.. send_draft = %s' % (send_draft))

    app_bridges = []
    app_worker = None
    try:
        app_worker = AppWorker(workdir).connect()

        # Request "locked" status from the app.
        app_crypto_status = app_worker.call('rpc/crypto_status')
        app_is_locked = app_crypto_status.get('locked')
        logging.debug('crypto status: %s' % (app_crypto_status,))

        screen = urwid.raw_display.Screen()
        aev_loop = asyncio.get_event_loop()
        conn_manager = TuiConnManager(aev_loop, app_worker)
        tui_frame = TuiFrame(screen, conn_manager)

        initial_state = {'app_is_locked': app_is_locked}

        if send_draft:
            from moggie.email.draft import MessageDraft
            initial_state['show_draft'] = send_draft

        elif '-f' in tui_args:
            # Display the contents of a mailbox or folder; this should
            # always be possible whether the app is locked or not.
            target = os.path.abspath(tui_args['-f'])
            if '-y' in tui_args:
                initial_state['show_browser'] = target
            elif target.endswith('/'):
                initial_state['show_browser'] = target
            else:
                initial_state['show_mailbox'] = target

        tui_frame.set_initial_state(initial_state)
        urwid.MainLoop(urwid.AttrMap(tui_frame, 'body'),
            palette(app_worker.app.config),
            pop_ups=True,
            screen=screen,
            handle_mouse=False,
            event_loop=urwid.AsyncioEventLoop(loop=aev_loop),
            unhandled_input=tui_frame.unhandled_input
            ).run()

    except KeyboardInterrupt:
        pass
    finally:
        conn_manager.close()
        # FIXME: Should we kill the backend if another client is using it.
        #        Should we at least ask the user?
        if app_worker and app_worker.is_alive():
            app_worker.quit()
        logging.info('Stopped %s v%s text UI with pid=%d'
            % (APPNAME, APPVER, os.getpid()))
