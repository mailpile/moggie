import asyncio
import json
import logging
import os
import re
import random
import sys
import time
import urwid

import websockets
import websockets.exceptions

from ...config import AppConfig, APPNAME, APPVER, configure_logging
from ...jmap.core import JMAPSessionResource
from ...jmap.requests import *
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker

from .decorations import palette
from .tui_frame import TuiFrame


DEFAULT_LOG_LEVEL = 5  # FIXME: Should be 2


def Main(workdir, sys_args, tui_args, send_args):
    loglevel = max(0, min(int(sys_args.get('-d', DEFAULT_LOG_LEVEL)), 4))
    logfile = configure_logging(
        stdout=False,
        profile_dir=workdir,
        level=[
            logging.CRITICAL,
            logging.ERROR,
            logging.WARNING,
            logging.INFO,
            logging.DEBUG
            ][loglevel])
    if loglevel > 2:
        sys.stderr.write('Logging to %s (startup in 2s)\n' % (logfile,))
        time.sleep(2)
    logging.info('Starting %s v%s text UI with pid=%d, loglevel=%d'
        % (APPNAME, APPVER, os.getpid(), loglevel))
    logging.debug('.. sys_args = %s' % (sys_args))
    logging.debug('.. tui_args = %s' % (tui_args))
    logging.debug('.. send_args = %s' % (send_args))

    app_bridge = app_worker = None
    try:
        app_worker = AppWorker(workdir).connect()

        # Request "locked" status from the app.
        app_crypto_status = app_worker.call('rpc/crypto_status')
        app_is_locked = app_crypto_status.get('locked')
        logging.debug('crypto status: %s' % (app_crypto_status,))

        screen = urwid.raw_display.Screen()
        tui_frame = TuiFrame(screen, app_is_locked)
        aev_loop = asyncio.get_event_loop()
        app_bridge = AsyncRPCBridge(aev_loop, 'app', app_worker, tui_frame)

        if not app_is_locked:
            jsr = JMAPSessionResource(app_worker.call('rpc/jmap_session'))
            logging.debug('jmap sessions: %s' % (jsr,))
            # Request list of available JMAP Sessions from the app.
            # Establish a websocket/JMAP connection to each remote Session.
            # Populate sidebar.
            pass  # FIXME

        if send_args['_order']:
            # Display the composer
            # (Note, if locked, then "send" will just queue the messasge)
            pass  # FIXME

        elif '-f' in tui_args:
            # Display the contents of a mailbox; this should always be
            # possible whether app is locked or not.
            #
            # FIXME: incomplete, we need to also ensure that Context Zero
            # is selected. Is setting expanded=0 reliably that?
            tui_frame.show_mailbox(
                os.path.abspath(tui_args['-f']),
                AppConfig.CONTEXT_ZERO)
            tui_frame.context_list.expanded = 0

        elif not app_is_locked:
            # At this stage, we know the app is unlocked, but we don't
            # know what Contexts are available; so we should just set a
            # flag to "show defaults" which gets acted upon when we have
            # a bit more context available.
            #
            # This would probably default to Context 0/INBOX, but the user
            # should be able to override that somehow (explicitly or not)
            pass

        else:
            # Display locked screen
            pass # FIXME

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
        if app_bridge:
            # FIXME: This is probably not enough
            app_bridge.keep_running = False
        if app_worker and app_worker.is_alive():
            app_worker.quit()
        logging.info('Stopped %s v%s text UI with pid=%d'
            % (APPNAME, APPVER, os.getpid()))
