import asyncio
import datetime
import json
import logging
import time
import urwid

from ...config import APPNAME, APPVER
from ...jmap.requests import *
from ..suggestions import Suggestion, SuggestionWelcome

from .contextlist import ContextList
from .emaillist import EmailList
from .changepassdialog import ChangePassDialog
from .unlockdialog import UnlockDialog
from .searchdialog import SearchDialog
from .suggestionbox import SuggestionBox
from .widgets import *


def _w(w, attr={}, valign='top'):
    return urwid.AttrWrap(urwid.Filler(w, valign=valign), attr)


class PopUpManager(urwid.PopUpLauncher):
    def __init__(self, tui_frame, content):
        super().__init__(content)
        self.tui_frame = tui_frame
        self.target = None
        self.target_args = []

    def open_with(self, target, *target_args):
        self.target = target
        self.target_args = target_args
        return self.open_pop_up()

    def create_pop_up(self):
        target, args = self.target, self.target_args
        if self.target:
            pop_up = self.target(self.tui_frame, *self.target_args)
            urwid.connect_signal(pop_up, 'close', lambda b: self.close_pop_up())
            return pop_up
        return None

    def get_pop_up_parameters(self):
        # FIXME: Make this dynamic somehow?
        cols, rows = self.tui_frame.screen.get_cols_rows()
        wwidth = min(cols, self.target.WANTED_WIDTH)
        return {
            'left': (cols//2)-(wwidth//2),
            'top': 2,
            'overlay_width': wwidth,
            'overlay_height': self.target.WANTED_HEIGHT}


class TuiFrame(urwid.Frame):
    def __init__(self, screen, app_is_locked):
        self.screen = screen
        self.is_locked = app_is_locked
        self.was_locked = app_is_locked
        self.render_cols_rows = self.screen.get_cols_rows()
        self.app_bridge = None

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
        self.context_list = ContextList(self, [])

        self.all_columns = [self.context_list]
        self.topbar_pile = urwid.Pile([])
        self.topbar = PopUpManager(self, self.topbar_pile)

        self.update_topbar(update=False)
        self.update_columns(update=False, focus=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)
        self.contents['header'] = (self.topbar, None)

        loop = asyncio.get_event_loop()
        loop.create_task(self.topbar_clock())

    current_context = property(lambda s: s.context_list.active)

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

    def handle_bridge_message(self, bridge_name, message):
        try:
            message = json.loads(message)
            for widget in self.all_columns:
                if hasattr(widget, 'incoming_message'):
                    try:
                        widget.incoming_message(message)
                    except:
                        logging.exception('Incoming message asploded')

            if message.get('prototype') in ('notification', 'unlocked'):
                if message['prototype'] == 'unlocked':
                    self.is_locked = False
                self.notifications.append(message)
                self.update_topbar()
            elif message.get('internal_websocket_error'):
                if message.get('count', 0) > 3:
                    msg = ('Worker (%s) is unreachable. Is the network down?'
                        % bridge_name)
                    self.notifications.append({
                        'message': msg,
                        'ts': time.time(),
                        'data': message})
                    self.update_topbar()
        except:
            logging.exception('Exception handling message: %s' % (message,))

    def link_bridge(self, app_bridge):
        self.app_bridge = app_bridge
        return self.handle_bridge_message

    def set_context(self, contexts, i):
        # FIXME: Do we really need to recreate the context list?
        self.context_list = ContextList(self, contexts, expanded=i)
        self.all_columns[0] = self.context_list
        self.update_columns()

    def show_mailbox(self, which, context=None):
        if context is None:
            context = self.context_list.active
        self.col_show(self.all_columns[0],
            EmailList(self, RequestMailbox(context, which)))

    def show_search_result(self, terms, context=None):
        if context is None:
            context = self.context_list.active
        self.col_show(self.all_columns[0],
            EmailList(self, RequestSearch(context, terms)))

    def ui_quit(self):
        raise urwid.ExitMainLoop()

    def unlock(self, passphrase):
        logging.info('Passphrase supplied, attempting unlock')
        self.app_bridge.send_json(RequestUnlock(passphrase))

    def ui_change_passphrase(self):
        self.topbar.open_with(ChangePassDialog)

    def change_passphrase(self, old_passphrase, new_passphrase,
            disconnect=False):
        logging.info(
            'New passphrase supplied, requesting change (disconnect=%s)'
            % (disconnect,))
        self.app_bridge.send_json(RequestChangePassphrase(
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
        return ' \U0001F512' if self.is_locked else ''

    def update_topbar(self, update=True):
        # FIXME: Calculate/hint hotkeys based on what our columns suggest?
        now = time.time()

        maxwidth = self.render_cols_rows[0] - 2
        crumbtrail = ' -> '.join(self.crumbs)
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
            push = (' ' * int((30 - nage) * (30 - nage) // 120))
            msg = self.notifications[-1]['message']
            hints = [
                ('weight', len(msg), urwid.Text(
                    push + msg, align='left', wrap='clip'))]
        else:
            nage = 0
            hints.append(
                ('weight', len(clock), urwid.Text(('subtle', clock),
                    align='center')))

        if not nage or (maxwidth > 70 + 8*(3+len(global_hks))):
            # FIXME: Calculate actual width and use that.
            search = [] if self.is_locked else [('top_hk', '/:'), 'Search ']
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
                urwid.Text(crumbtrail, align='left'),
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
                return key
        except IndexError:
            return key
