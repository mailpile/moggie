import asyncio
import logging
import os
import re
import time
import urwid

from moggie.app import Nonsense

from ...config import APPNAME, APPVER, configure_logging

from .decorations import palette
from .tui_frame import TuiFrame


def Main(moggie, tui_args, send_draft):
    for arg in ('-E', '-p', '-R', '-Z'):
        if arg in tui_args:
            raise Nonsense('FIXME: Unimplemented: moggie %s' % (arg,))

    logging.info('Starting %s v%s text UI with pid=%d'
        % (APPNAME, APPVER, os.getpid()))
    logging.debug('.. tui_args = %s' % (tui_args))
    logging.debug('.. send_draft = %s' % (send_draft))

    # We are using Moggie in the Pythonic way, not for a CLI.
    moggie.set_mode(moggie.MODE_PYTHON)

    try:
        app_worker = None
        for tries in range(0, 5):
            app_worker = moggie.connect()._app_worker
            if app_worker:
                break
            logging.error('Launch/connect failed, this should not happen')
            time.sleep(0.25)
        if not app_worker:
            raise Nonsense('Failed to connect/launch app worker')

        # Get an event loop, connect the websocket. We need this right
        # away, as the TUI will immediately start sending bootstrapping
        # queries to the backend when it is created.
        aev_loop = asyncio.get_event_loop()
        aev_loop.run_until_complete(moggie.enable_websocket(aev_loop))

        # Request "locked" status from the app.
        # FIXME: use the Moggie API
        app_crypto_status = app_worker.call('rpc/crypto_status')
        app_is_locked = app_crypto_status.get('locked')
        initial_state = {'app_is_locked': app_is_locked}

        if send_draft:
            initial_state['show_draft'] = send_draft

        elif '-f' in tui_args:
            # Display the contents of a mailbox or folder; this should
            # always be possible whether the app is locked or not.
            target = tui_args['-f']
            as_dir = target.endswith('/')
            if target.startswith('imap:'):
                if '/' not in target[7:]:
                    as_dir = True
            else:
                target = os.path.abspath(target)

            if '-y' in tui_args:
                initial_state['show_browser'] = target
            elif as_dir:
                initial_state['show_browser'] = target
            else:
                initial_state['show_mailbox'] = target

        elif '-y' in tui_args:
            initial_state['show_browser'] = True

        screen = urwid.raw_display.Screen()
        main_frame = TuiFrame(moggie, screen)
        main_frame.set_initial_state(initial_state)

        main_frame.main_loop = urwid.MainLoop(
            urwid.AttrMap(main_frame, 'body'),
            palette(app_worker.app.config),
            pop_ups=True,
            screen=screen,
            handle_mouse=False,
            event_loop=urwid.AsyncioEventLoop(loop=aev_loop),
            unhandled_input=main_frame.unhandled_input)

        main_frame.main_loop.run()

    except KeyboardInterrupt:
        pass
    finally:
        # FIXME: Should we kill the backend if another client is using it.
        #        Should we at least ask the user?
        if app_worker and app_worker.is_alive():
            logging.debug('Stopping app worker')
            app_worker.quit()
        logging.info('Stopped %s v%s text UI with pid=%d'
            % (APPNAME, APPVER, os.getpid()))
