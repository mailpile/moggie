import asyncio
import logging
import os
import re
import sys
import time
import urwid

import websockets
import websockets.exceptions

from ...config import APPNAME, APPVER, configure_logging
from ...api.requests import *
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker

from .decorations import palette
from .tui_frame import TuiFrame


def Main(workdir, tui_args, send_draft):
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
        tui_frame = TuiFrame(screen)
        app_bridges = [AsyncRPCBridge(aev_loop, 'app', app_worker, tui_frame)]

        initial_state = {
            'app_is_locked': app_is_locked,
            'app_bridges': app_bridges}

        if send_draft:
            from moggie.email.draft import MessageDraft
            initial_state['show_draft'] = send_draft

        elif '-f' in tui_args:
            # Display the contents of a mailbox; this should always be
            # possible whether app is locked or not.
            initial_state['show_mailbox'] = tui_args['-f']

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
        for app_bridge in app_bridges:
            # FIXME: This is probably not enough
            app_bridge.keep_running = False
        if app_worker and app_worker.is_alive():
            app_worker.quit()
        logging.info('Stopped %s v%s text UI with pid=%d'
            % (APPNAME, APPVER, os.getpid()))
