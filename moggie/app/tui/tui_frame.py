# This is the main application "window manager"; it manages the UI
# from a high level and holds everything together.

import asyncio
import copy
import datetime
import logging
import json
import os
import time
import urwid

from moggie import MoggieContext

from ...config import APPNAME, APPVER
from ...api.requests import *
from ..suggestions import Suggestion, SuggestionWelcome

from .decorations import EMOJI
from .browser import Browser
from .changepassdialog import ChangePassDialog
from .contextlist import ContextList
from .emaillist import EmailList
from .composer import Composer
from .godialog import GoDialog
from .retrydialog import RetryDialog
from .searchdialog import SearchDialog
from .suggestionbox import SuggestionBox
from .undodialog import UndoDialog
from .unlockdialog import UnlockDialog
from .widgets import *


def _w(w, attr={}, valign='top'):
    return urwid.AttrWrap(urwid.Filler(w, valign=valign), attr)


class TuiFrame(urwid.Frame):

    current_context = property(lambda s: s.context_list.active['key'])

    def __init__(self, moggie, screen):
        self.screen = screen
        self.is_locked = True
        self.was_locked = True
        self.user_moved = False
        self.render_cols_rows = self.screen.get_cols_rows()
        self.moggie = moggie
        self.mog_ctx0 = MoggieContext(moggie)
        self.main_loop = None

        suggestions = SuggestionBox(self,
            fallbacks=[SuggestionWelcome],
            max_suggestions=5)

        self.filler1 = SplashCat(suggestions, 'Welcome to Moggie!')
        self.filler2 = SplashMoreWide()
        self.filler3 = SplashMoreNarrow()

        self.hidden = 0
        self.crumbs = []
        self.secrets = {}
        self.undoable = []
        self.notifications = []
        self.columns = urwid.Columns([self.filler1], dividechars=1)
        self.context_list = ContextList(self,
            first=False, update_parent=self.update_columns)

        self.all_columns = [self.context_list]
        self.topbar_pile = urwid.Pile([])
        self.topbar = PopUpManager(self, self.topbar_pile)

        self.update_columns(update=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)
        self.contents['header'] = (self.topbar, None)

        loop = asyncio.get_event_loop()
        loop.create_task(self.topbar_clock())

        moggie.on_notification(self.handle_moggie_messages)
        moggie.on_error(self.handle_moggie_messages)

    def set_initial_state(self, initial_state):
        self.is_locked = initial_state.get('app_is_locked')
        self.was_locked = self.is_locked

        show_draft = initial_state.get('show_draft')
        show_browser = initial_state.get('show_browser')
        show_mailbox = initial_state.get('show_mailbox')

        if show_draft:
            # Display the composer; whether we are locked or not.
            # But what happens to any composed mail will vary!
            self.show_composer(self.mog_ctx0, show_draft)

        elif show_browser:
            self.show_browser(self.mog_ctx0, show_browser, history=False)

        elif show_mailbox:
            self.show_mailbox(self.mog_ctx0, show_mailbox)

        else:
            # What the default view is, depends on what the context
            # has configured. Let the ContextList figure it out?
            # Or is that terrible UX and we value consistency?
            # self.context_list.activate_default_view()
            self.columns.set_focus_path([1]) # Focus the cat!

    async def topbar_clock(self):
        while True:
            self.update_topbar()
            if self.notifications:
                await asyncio.sleep(0.25)
            else:
                await asyncio.sleep(1)

            now = time.time()
            self.notifications = [
                n for n in self.notifications if n['ts'] > (now-20)]

    def show_modal(self, cls, *args, **kwargs):
        return self.topbar.open_with(cls, *args, **kwargs)

    def showing_modal(self):
        return self.topbar.showing_popup()

    def request_failed_modal(self, error):
        pass

    def handle_exceptions(self, moggie, message):
        needed = message['exc_data'].get('need')
        if needed:
            # If a password or other details are needed, we first check
            # whether we have them cached; if so we try again right away using
            # the cached values. If not, we show the user a RetryDialog.
            #
            # The Moggie object does not immediately delete handlers on error,
            # so simply resending the request with new details will work and
            # the response get routed to the right place. We add a callback
            # to update our secret cache if (and only if) the call succeeds.
            #
            resource = message['exc_data'].get('resource')
            retry = message.get('request')

            # Pop (remove) from the cache, in case it is no longer correct.
            # On success we add it back.
            cached = self.secrets.pop(resource, None) if resource else None

            def do_retry(update):
                add_to_cache = self.secrets.__setitem__
                retry.update(update)
                moggie.websocket_send(retry,
                    on_success=lambda m,r: add_to_cache(resource, update))

            if cached:
                do_retry(cached)
            else:
                self.show_modal(RetryDialog, moggie, message, do_retry)

    def handle_moggie_messages(self, moggie, message):
        try:
            if 'error' in message and 'exception' in message:
                self.handle_exceptions(moggie, message)

                # FIXME: Display other errors? Or no?

            elif message.get('req_type') in ('notification', 'unlocked'):
                if message['req_type'] == 'unlocked':
                    self.is_locked = False
                    self.context_list.activate_default_view()
                self.notifications.append(message)

            else:
                logging.debug('Unhandled: %s' % message)

        except:
            logging.exception('Exception handling message: %s' % (message,))

    def active_mog_ctx(self):
        return self.context_list.active

    def set_context(self, i):
        self.context_list.expand(i)
        self.update_columns()

    def show_browser(self, mog_ctx, which=True, history=True):
        self.col_show(self.all_columns[0], Browser(mog_ctx, self, which))
        if history:
            label = 'Browse' if which is True else os.path.basename(which)
            self.context_list.add_history(
                label,
                lambda: self.show_browser(mog_ctx, which),
                icon=EMOJI.get('browsing', '-'))

    def show_mailbox(self, mog_ctx, which, history=True, keep=None):
        terms = 'mailbox:%s' % which
        column = keep or self.all_columns[0]
        self.col_show(column, EmailList(mog_ctx, self, terms))
        if history:
            self.context_list.add_history(
                os.path.basename(which),
                lambda: self.show_mailbox(mog_ctx, which),
                icon=EMOJI.get('mailbox', '-'))

    def show_search_result(self, mog_ctx, terms, history=True):
        # FIXME: The app should return an error and we retry
        if self.is_locked:
            self.show_modal(UnlockDialog)
            return

        self.col_show(self.all_columns[0], EmailList(mog_ctx, self, terms))
        if history:
            self.context_list.add_history(
                terms,
                lambda: self.show_search_result(mog_ctx, terms, True),
                icon=EMOJI.get('search', '-'))

    def show_composer(self, mog_ctx, draft_message=None, add=False):
        if self.is_locked:
            self.show_modal(UnlockDialog)
            return
        
        self.col_show(
            self.all_columns[-2 if add else 0],
            Composer(mog_ctx, self, draft_message))

    def show_preferences(self, mog_ctx):
        pass

    def refresh_all(self):
        for widget in self.all_columns:
            if hasattr(widget, 'refresh'):
                widget.refresh()
        self.redraw()

    def ui_quit(self):
        raise urwid.ExitMainLoop()

    def unlock(self, passphrase):
        logging.info('Passphrase supplied, attempting unlock')
        # FIXME: This should be in the high-level Moggie API
        self.moggie.websocket_send(RequestUnlock(passphrase))

    def ui_change_passphrase(self):
        self.show_modal(ChangePassDialog)

    def change_passphrase(self, old_passphrase, new_passphrase,
            disconnect=False):
        logging.info(
            'New passphrase supplied, requesting change (disconnect=%s)'
            % (disconnect,))
        # FIXME: This should be in the high-level Moggie API
        self.moggie.websocket_send(RequestChangePassphrase(
            old_passphrase, new_passphrase,
            disconnect=disconnect))

    def max_child_rows(self):
        return self.screen.get_cols_rows()[1] - 2

    def render(self, *args, **kwargs):
        # This lets us adapt our display to screen width;
        # hiding or showing columns as necessary.
        cols_rows = self.screen.get_cols_rows()
        if self.render_cols_rows != cols_rows:
            self.render_cols_rows = cols_rows
            for wdgt in self.all_columns:
                if hasattr(wdgt, 'update_content'):
                    wdgt.update_content()
            self.update_columns()
        return urwid.Frame.render(self, *args, **kwargs)

    def locked_emoji(self):
        return (' %s' % EMOJI.get('lock', '!')) if self.is_locked else ''

    def update_topbar(self, update=True):
        # FIXME: Calculate/hint hotkeys based on what our columns suggest?
        maxwidth = self.render_cols_rows[0] - 2
        now = time.time()

        self.crumbs = []
        for widget in self.all_columns:
            if hasattr(widget, 'crumb'):
                self.crumbs.append(widget.crumb)

        crumbs = copy.copy(self.crumbs)
        for i, crumb in enumerate(crumbs):
            if i < len(self.crumbs)-1 and maxwidth < 100:
                if crumb.endswith(')'):
                    crumbs[i] = crumb = crumb.rsplit(' (', 1)[0]
                if '/' in crumb:
                    crumbs[i] = crumb.rsplit('/')[-1]
        crumbshift = max(0, 17 - len(crumbs[0] if crumbs else ''))
        crumbtrail = (' ' * crumbshift) + ': '.join(crumbs)
        crumblen = len(crumbtrail)

        pad = ' ' if maxwidth > 80 else ''
        global_hks = []
        for col in self.all_columns:
            if hasattr(col, 'global_hks'):
                for hk in col.global_hks.values():
                    if isinstance(hk, list):
                        global_hks.extend(hk[1:])

        column_hints = []
        fpath = self.columns.get_focus_path()
        try:
            wdgt = self.all_columns[self.hidden + fpath[0]]
            if hasattr(wdgt, 'column_hks'):
                hks = wdgt.column_hks
                if not isinstance(hks, list):
                    hks = hks()
                if hks:
                    tw = urwid.Text(hks)
                    column_hints.append(('fixed', len(tw.text), tw))
        except IndexError:
            pass

        ntime = datetime.datetime.now()
        if maxwidth > 150:
            cfmt = '%s'
            clock_a = clock_b = '  %A, %Y-%m-%d  %H:%M:%S'
        elif maxwidth > 115:
            cfmt = '%s'
            clock_a = clock_b = ' %a %Y-%m-%d %H:%M:%S'
        elif maxwidth > 84:
            cfmt = '%14s'
            clock_a = '%a %H:%M:%S'
            clock_b = '%a %Y-%m-%d'
        elif maxwidth > 74:
            cfmt = '%10s'
            clock_a = '%a %H:%M'
            clock_b = '%Y-%m-%d'
        else:
            cfmt = '%5s'
            clock_a = '%H:%M'
            clock_b = '%a '
        if (now // 8) % 3 == 1:
            clock = cfmt % ntime.strftime(clock_b) + self.locked_emoji()
        else:
            clock = cfmt % ntime.strftime(clock_a) + self.locked_emoji()

        global_hints = []
        nage = 0
        if self.notifications:
            nage = now - self.notifications[-1]['ts']
        if 0 < nage <= 30:
            msg = self.notifications[-1]['message']
            global_hints = [('weight', len(msg),
                urwid.Text(msg, align='left', wrap='ellipsis'))]
        else:
            nage = 0
            global_hints.append(('weight', len(clock),
                urwid.Text(('subtle', clock), align='center')))

        if not nage or (maxwidth > 70 + 8*(3+len(global_hks))):
            # FIXME: Calculate actual width and use that.
            search = [] if self.is_locked else [('top_hk', '/:'), 'Search ']
            unlock = [('top_hk', '/:'), 'Unlock '] if self.is_locked else []
            global_hints.extend([
                ('fixed', 23+6*len(global_hks), urwid.Text(
                    global_hks + search + unlock + [
#FIXME:                 ('top_hk', '?:'), 'Help ',
                        ('top_hk', 'g:'), 'Go ',
                        ('top_hk', 'Q:'), 'Quit'+pad],
                    align='right', wrap='clip'))])

        mv = '%s%s v%s ' % (pad, APPNAME, APPVER)

        _p = lambda w: (w, ('pack', None))
        self.topbar_pile.contents = [
            _p(urwid.AttrMap(urwid.Columns([
                ('fixed', len(mv), urwid.Text(mv, align='left')),
                ] + global_hints), 'header')),
            _p(urwid.AttrMap(urwid.Columns([
                ('weight', crumblen, urwid.Text(crumbtrail, wrap='ellipsis'))
                ] + column_hints, dividechars=1), 'crumbs'))]
        #if update:
        #    self.contents['header'] = (self.topbar, None)

    def focus_last_column(self):
        try:
            last = len(self.all_columns) - self.hidden - 1
            self.columns.set_focus_path([last])
        except IndexError:
            pass  #logging.exception('Focus last failed')

    def col_show(self, ref, widget):
        logging.debug('Adding %s' % widget)
        self.col_remove(ref, ofs=1, update=False)
        self.all_columns.append(widget)
        self.update_columns()
        self.focus_last_column()

    def col_replace(self, ref, widget):
        self.col_remove(ref, update=False)
        self.all_columns.append(widget)
        self.update_columns()
        self.focus_last_column()

    def col_remove(self, ref, ofs=0, update=True):
        pos = self.all_columns.index(ref)
        if pos >= 0:
            pos += ofs
            if pos > 0:
                for widget in self.all_columns[pos:]:
                    if hasattr(widget, 'cleanup'):
                        widget.cleanup()
                self.all_columns[pos:] = []
            if update:
                self.update_columns()
                self.focus_last_column()

    def update_columns(self, update=True):
        cols, rows = self.screen.get_cols_rows()
        try:
            focus_path = self.get_focus_path()
        except AttributeError:
            focus_path = None

        self.hidden = 0
        widgets = []
        widgets.extend(self.all_columns)
        while sum(col.COLUMN_NEEDS for col in widgets) > cols:
            widgets = widgets[1:]
            self.hidden += 1
            focus_path = None

        # Add our cute fillers, if we have screen real-estate to burn.
        used = sum(col.COLUMN_NEEDS for col in widgets)
        if used + self.filler1.COLUMN_NEEDS < cols and (len(widgets) < 2):
            widgets.append(self.filler1)
            used += self.filler1.COLUMN_NEEDS
        if used + self.filler2.COLUMN_NEEDS < cols and (len(widgets) < 3):
            widgets.append(self.filler2)
            used += self.filler2.COLUMN_NEEDS
        if used + self.filler3.COLUMN_NEEDS < cols:
            widgets.append(self.filler3)
            used += self.filler3.COLUMN_NEEDS

        def _b(w):
            if hasattr(w, 'rows'):
                widget = _w(w, w.COLUMN_STYLE)
            else:
                widget = _w(urwid.BoxAdapter(w, rows-2), w.COLUMN_STYLE)
            return (w.COLUMN_FIT, w.COLUMN_WANTS, widget)
        columns = [_b(c) for c in widgets]

        self.columns = urwid.Columns(columns, dividechars=1)
        self.update_topbar(update=update)
        if update:
            self.contents['body'] = (self.columns, None)
            try:
                if focus_path:
                    self.set_focus_path(focus_path)
            except (IndexError, AttributeError):
                pass

        self.redraw()

    def redraw(self):
        if self.main_loop:
            self.main_loop.draw_screen()

    def keypress(self, size, key):
        if key in ('q', 'esc', 'left', 'right', 'up', 'down'):
            self.user_moved = True
        try:
            return super().keypress(size, key)
        except AttributeError:
            logging.exception('FIXME: Urwid bug in keypress handler?')
            self.focus_last_column()

    def unhandled_input(self, key):
        try:
            if key in ('q', 'esc', 'left', 'right', 'up', 'down'):
                self.user_moved = True

            cols_rows = self.screen.get_cols_rows()
            if key == 'Q':
                self.ui_quit()
            elif self.showing_modal():
                if key in ('esc',):
                    self.topbar.target._emit('close')
                return key

            if key in ('q', 'esc', 'backspace'):
                if len(self.all_columns) > 1:
                    self.col_remove(self.all_columns[-1])
                elif key == 'q':
                    self.ui_quit()
            elif key == 'left':
                if len(self.all_columns) > 1 and self.hidden:
                    self.col_remove(self.all_columns[-1])
            elif key == 'right':
                pass  #self.columns.keypress(cols_rows, 'enter')

            # FIXME: Searching or unlocking is a global thing
            elif key == '/':
                if self.is_locked:
                    self.show_modal(UnlockDialog)
                else:
                    self.show_modal(SearchDialog)
                return None

            elif key in ('g', '?'):
                self.show_modal(GoDialog)

            elif key == 'z':
                if self.undoable:
                    self.show_modal(UndoDialog)

            # FIXME: This definitely belongs elsewhere!
            elif key == 'C':
                self.ui_change_passphrase()
                return None

            # hjkl navigation
            elif key == 'h':
                if len(self.all_columns) > 1 and self.hidden:
                    self.col_remove(self.all_columns[-1])
                    return None
                else:
                    return self.keypress(cols_rows, 'left')
            elif key == 'j':
                return self.keypress(cols_rows, 'down')
            elif key == 'k':
                return self.keypress(cols_rows, 'up')
            elif key == 'l':
                return self.keypress(cols_rows, 'right')

            else:
                for col in self.all_columns:
                    if hasattr(col, 'global_hks') and key in col.global_hks:
                        return col.keypress(cols_rows, key)
                return key
        except IndexError:
            return key
