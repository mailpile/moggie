# This is the main application "window manager"; it manages the UI
# from a high level and holds everything together.

import asyncio
import datetime
import logging
import json
import os
import time
import urwid

from ...config import APPNAME, APPVER, AppConfig
from ...api.requests import *
from ..suggestions import Suggestion, SuggestionWelcome

from .decorations import EMOJI
from .browser import Browser
from .contextlist import ContextList
from .emaillist import EmailList
from .changepassdialog import ChangePassDialog
from .unlockdialog import UnlockDialog
from .retrydialog import RetryDialog
from .searchdialog import SearchDialog
from .suggestionbox import SuggestionBox
from .widgets import *


def _w(w, attr={}, valign='top'):
    return urwid.AttrWrap(urwid.Filler(w, valign=valign), attr)


class TuiFrame(urwid.Frame):

    current_context = property(lambda s: s.context_list.active['key'])

    def __init__(self, screen, conn_manager):
        self.screen = screen
        self.is_locked = True
        self.was_locked = True
        self.render_cols_rows = self.screen.get_cols_rows()
        self.conn_manager = conn_manager

        suggestions = SuggestionBox(self,
            fallbacks=[SuggestionWelcome],
            max_suggestions=5)

        self.filler1 = SplashCat(suggestions, 'Welcome to Moggie!')
        self.filler2 = SplashMoreWide()
        self.filler3 = SplashMoreNarrow()

        self.hidden = 0
        self.crumbs = []
        self.notifications = []
        self.columns = urwid.Columns([self.filler1], dividechars=1)
        self.context_list = ContextList(self,
            first=False, update_parent=self.update_columns)

        self.all_columns = [self.context_list]
        self.topbar_pile = urwid.Pile([])
        self.topbar = PopUpManager(self, self.topbar_pile)

        self.update_topbar(update=False)
        self.update_columns(update=False, focus=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)
        self.contents['header'] = (self.topbar, None)

        loop = asyncio.get_event_loop()
        loop.create_task(self.topbar_clock())

        conn_manager.add_handler('tui', '*', '*', self.handle_bridge_messages)

    def set_initial_state(self, initial_state):
        self.is_locked = initial_state.get('app_is_locked')
        self.was_locked = self.is_locked

        show_draft = initial_state.get('show_draft')
        show_browser = initial_state.get('show_browser')
        show_mailbox = initial_state.get('show_mailbox')

        if show_draft:
            # Display the composer; whether we are locked or not.
            # But what happens to any composed mail will vary!
            pass  # FIXME

        elif show_browser:
            self.show_browser(show_browser)

        elif show_mailbox:
            self.show_mailbox(show_mailbox)

        else:
            # What the default view is, depends on what the context
            # has configured. Let the ContextList figure it out.
            self.context_list.activate_default_view()

    async def topbar_clock(self):
        while True:
            self.update_topbar()
            if self.notifications:
                await asyncio.sleep(0.25)
            else:
                await asyncio.sleep(1)
            expired = time.time() - 20
            self.notifications = [
                n for n in self.notifications if n['ts'] > expired]

    def send_with_context(self, message_obj, context=None):
        return self.context_list.send_with_context(message_obj, context)

    def handle_bridge_messages(self, bridge_name, message):
        try:
            for widget in self.all_columns:
                if hasattr(widget, 'handle_bridge_messages'):
                    try:
                        widget.handle_bridge_messages(bridge_name, message)
                    except:
                        logging.exception('Incoming message asploded')

            if message.get('error') and message.get('exception'):
                self.topbar.open_with(RetryDialog, message)

            elif message.get('req_type') in ('notification', 'unlocked'):
                if message['req_type'] == 'unlocked':
                    self.is_locked = False
                    self.context_list.activate_default_view()
                self.notifications.append(message)

        except:
            logging.exception('Exception handling message: %s' % (message,))

    def set_context(self, i):
        self.context_list.expand(i)
        self.update_columns()

    def show_browser(self, which, context=None, history=True):
        ctx_id, ctx_src_id = self.context_list.get_context_and_src_ids(context)
        self.col_show(self.all_columns[0],
            Browser(self, RequestBrowse(ctx_id, which), ctx_src_id))
        if history:
            self.context_list.add_history(
                os.path.basename(which),
                lambda: self.show_browser(which, context),
                icon=EMOJI.get('browsing', '-'))

    def show_mailbox(self, which, context=None, history=True):
        _, ctx_src_id = self.context_list.get_context_and_src_ids(context)
        terms = 'mailbox:%s' % which
        self.col_show(self.all_columns[0], EmailList(self, ctx_src_id, terms))

        if history:
            self.context_list.add_history(
                os.path.basename(which),
                lambda: self.show_mailbox(which, context),
                icon=EMOJI.get('mailbox', '-'))

    def show_search_result(self, terms, context=None, history=True):
        # FIXME: The app should return an error and we retry
        if self.is_locked:
            self.topbar.open_with(UnlockDialog)
            return

        _, ctx_src_id = self.context_list.get_context_and_src_ids(context)
        self.col_show(self.all_columns[0], EmailList(self, ctx_src_id, terms))
        if history:
            self.context_list.add_history(
                terms,
                lambda: self.show_search_result(terms, context, True),
                icon=EMOJI.get('search', '-'))

    def ui_quit(self):
        raise urwid.ExitMainLoop()

    def unlock(self, passphrase):
        logging.info('Passphrase supplied, attempting unlock')
        self.conn_manager.send(RequestUnlock(passphrase))

    def ui_change_passphrase(self):
        self.topbar.open_with(ChangePassDialog)

    def change_passphrase(self, old_passphrase, new_passphrase,
            disconnect=False):
        logging.info(
            'New passphrase supplied, requesting change (disconnect=%s)'
            % (disconnect,))
        self.conn_manager.send(RequestChangePassphrase(
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
        now = time.time()

        maxwidth = self.render_cols_rows[0] - 2
        crumbtrail = ': '.join(self.crumbs)
        if len(crumbtrail) > maxwidth:
            crumbtrail = '...' + crumbtrail[-(maxwidth-3):]

        pad = ' ' if maxwidth > 80 else ''

        global_hks = []
        column_hks = []
        selection_hks = []
        for col in self.all_columns:
            if hasattr(col, 'global_hks'):
                for hk in col.global_hks.values():
                    global_hks.extend(hk[1:])  # hk[0] is the callback
        for wdgt in self.columns.get_focus_widgets():
            if hasattr(wdgt, 'column_hks'):
                column_hks.extend(wdgt.column_hks)
            if hasattr(wdgt, 'selection_hks'):
                selection_hks.extend(wdgt.selection_hks)

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

        hints = []
        nage = 0
        if self.notifications:
            nage = now - self.notifications[-1]['ts']
        if 0 < nage <= 30:
            msg = self.notifications[-1]['message']
            hints = [('weight', len(msg),
                urwid.Text(msg, align='left', wrap='clip'))]
        else:
            nage = 0
            hints.append(('weight', len(clock),
                urwid.Text(('subtle', clock), align='center')))

        if not nage or (maxwidth > 70 + 8*(3+len(global_hks))):
            # FIXME: Calculate actual width and use that.
            search = [] if self.is_locked else [('top_hk', '/:'), 'Search ']
            search = []  # FIXME
            unlock = [('top_hk', '/:'), 'Unlock '] if self.is_locked else []
            hints.extend([
                ('fixed', 23+6*len(global_hks), urwid.Text(
                    global_hks + search + unlock + [
                        ('top_hk', '?:'), 'Help ',
                        ('top_hk', 'q:'), 'Quit'+pad],
                    align='right', wrap='clip'))])

        mv = '%s%s v%s ' % (pad, APPNAME, APPVER)

        _p = lambda w: (w, ('pack', None))
        self.topbar_pile.contents = [
            _p(urwid.AttrMap(urwid.Columns([
                    ('fixed', len(mv), urwid.Text(mv, align='left')),
                    ] + hints + [
                ]), 'header')),
            _p(urwid.AttrMap(urwid.Columns([
                urwid.Text((' ' * 19) + crumbtrail, align='left', wrap='clip'),
                ]), 'crumbs'))]
        #if update:
        #    self.contents['header'] = (self.topbar, None)

    def focus_last_column(self):
        try:
            self.columns.set_focus_path(
                [len(self.all_columns) - self.hidden - 1])
        except IndexError:
            pass

    def col_show(self, ref, widget):
        self.col_remove(ref, ofs=1, update=False)
        self.all_columns.append(widget)
        self.update_columns(focus=False)
        self.focus_last_column()

    def col_replace(self, ref, widget):
        self.col_remove(ref, update=False)
        self.all_columns.append(widget)
        self.update_columns(focus=False)
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

    def update_columns(self, update=True, focus=True):
        cols, rows = self.screen.get_cols_rows()

        self.hidden = 0
        widgets = []
        widgets.extend(self.all_columns)
        while sum(col.COLUMN_NEEDS for col in widgets) > cols:
            widgets = widgets[1:]
            self.hidden += 1

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

        self.crumbs = []
        for widget in self.all_columns:
            if hasattr(widget, 'crumb'):
                self.crumbs.append(widget.crumb)

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

    def unhandled_input(self, key):
        try:
            cols_rows = self.screen.get_cols_rows()
            if key == 'q':
                self.ui_quit()
            elif key == 'esc':
                if len(self.all_columns) > 1:
                    self.col_remove(self.all_columns[-1])
            elif key == 'left':
                if len(self.all_columns) > 1 and self.hidden:
                    self.col_remove(self.all_columns[-1])
            elif key == 'right':
                self.columns.keypress(cols_rows, 'enter')

            # FIXME: I am sure there must be a better way to do this.
            elif key == '/':
                if self.is_locked:
                    self.topbar.open_with(UnlockDialog)
                else:
                    self.topbar.open_with(SearchDialog)
            elif key == 'C':
                self.ui_change_passphrase()
            elif key == 'h':
                if len(self.all_columns) > 1 and self.hidden:
                    self.col_remove(self.all_columns[-1])
                else:
                    self.columns.keypress(cols_rows, 'left')
            elif key == 'j':
                self.columns.keypress(cols_rows, 'down')
            elif key == 'k':
                self.columns.keypress(cols_rows, 'up')
            elif key == 'l':
                self.columns.keypress(cols_rows, 'right')
            elif key == 'J':
                self.all_columns[1].listbox.keypress(cols_rows, 'down')
                self.all_columns[1].listbox.keypress(cols_rows, 'enter')
            elif key == 'K':
                self.all_columns[1].listbox.keypress(cols_rows, 'up')
                self.all_columns[1].listbox.keypress(cols_rows, 'enter')
            elif key in (' ',):
                self.all_columns[1].listbox.keypress(cols_rows, key)
            else:
                for col in self.all_columns:
                    if hasattr(col, 'hotkeys') and key in col.hotkeys:
                        col.hotkeys[key](None)
                        return
                return key
        except IndexError:
            return key
