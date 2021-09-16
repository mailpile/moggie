import asyncio
import random
import time
import urwid

import websockets
import websockets.exceptions

from ...config import APPNAME, APPVER
from ...email.metadata import Metadata
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker, test_contexts, test_emails


ENVELOPES = ("""\
     _______      x
    |==   []|     x
    |  ==== |____ x
    '-------'  []|x
         |   === |x
         '-------'x
  _______         x
 |==   []|        x
 |  ==== |        x
 '-------'        x
""").replace('x', '')

HELLO = ("""\
  _                        x
  \`*-.                    x
   )  _`-.                 x
  .  : `. .                x
  : _   '  \               x
  ; *` _.   `*-._          x
  `-.-'          `-.       x
    ;       `       `.     x
    :.       .        \    x
    . \  .   :   .-'   .   x
    '  `+.;  ;  '      :   x
    :  '  |    ;       ;-. x
    ; '   : :`-:     _.`* ;x
.*' /  .*' ; .*`- +'  `*'  x
 `*-*   `*-*  `*-*'        x

%s v%s                     x
""").replace('x', '') % (APPNAME, APPVER)

HELLO_CREDITS = """
   (cat by Blazej Kozlowski)
"""


def _w(w, attr={}, valign='top'):
    return urwid.AttrWrap(urwid.Filler(w, valign=valign), attr)


class Selectable(urwid.WidgetWrap):
    def __init__(self, contents, on_select=None):
        self.contents = contents
        self.on_select = on_select
        self._focusable = urwid.AttrMap(self.contents, '', dict(
            ((a, 'focus') for a in [None,
                'email', 'subtle', 'hotkey', 'active', 'act_hk',
                'email_from', 'email_attrs', 'email_subject', 'email_date'])))
        super(Selectable, self).__init__(self._focusable)

    def selectable(self):
        return True

    def keypress(self, size, key):
        if self.on_select and key in ('enter',):
            self.on_select(self)
        return key


class EmailList(urwid.ListBox):
    def __init__(self, tui_frame, emails):
        self.emails = emails
        self.tui_frame = tui_frame

        def _sel_email(which):
            return lambda x: self.tui_frame.show_email(which)

        widgets = []
        for i, email in enumerate(emails):
            cols = urwid.Columns([
                ('weight', 15, urwid.Text(('email_from', email['from'].fn))),
                (6,            urwid.Text(('email_attrs', '(    )'))),
                ('weight', 27, urwid.Text(('email_subject', email['subject']))),
                (10,           urwid.Text(('email_date', '2021-01-01')))],
                dividechars=1)
            widgets.append(Selectable(cols, on_select=_sel_email(email)))

        urwid.ListBox.__init__(self, urwid.SimpleListWalker(widgets))


class ContextList(urwid.ListBox):
    def __init__(self, tui_frame, contexts, expanded=0):
        self.contexts = contexts
        self.expanded = expanded
        self.tui_frame = tui_frame

        def _sel_ctx(which):
            return lambda x: self.tui_frame.set_context(self.contexts, which)
        def _sel_email(which):
            return lambda x: self.tui_frame.show_account(which)
        def _sel_tag(which):
            return lambda x: self.tui_frame.show_tag(which)

        widgets = []
        for i, ctx in enumerate(contexts):

            sc = ('g%d:' % (i+1)) if (i < 8) else '   '
            ctx_name = urwid.Text([
                ('hotkey', sc),
                ('subtle', ctx['name'])], 'left', 'clip')

            if i == expanded:
                widgets.append(Selectable(urwid.AttrMap(ctx_name,
                    {None: 'active', 'subtle': 'active', 'hotkey': 'act_hk'}),
                    on_select=_sel_ctx(-1)))
                widgets.append(urwid.Text([
                    ('subtle', 'live:1')], 'right', 'clip'))
            else:
                widgets.append(Selectable(ctx_name, on_select=_sel_ctx(i)))

            if i == expanded:
                for email in ctx['emails']:
                    widgets.append(Selectable(urwid.Padding(
                        urwid.Text(('email', email), 'left', 'clip'),
                        left=1, right=1)))
                widgets.append(urwid.Divider())
                for tg in ctx.get('tags', []):
                    for tag in tg:
                        if tag.get('count'):
                            sc = tag.get('sc', None)
                            sc = (' g%s:' % sc) if sc else '    '
                            widgets.append(Selectable(
                                urwid.Text([('hotkey', sc), tag['name']]),
                                on_select=_sel_tag(tag)))
                    widgets.append(urwid.Divider())

        if len(widgets) == 0:
            widgets.append(urwid.Text('\n\n(unconfigured) \n', 'center'))

        widgets.append(urwid.Text([('subtle', '_'*20)], 'left', 'clip'))
        widgets.append(Selectable(urwid.Text(
                [('hotkey', 'C:'), ('subtle', 'add context')], 'right'),
            on_select=lambda x: None))

        urwid.ListBox.__init__(self, urwid.SimpleListWalker(widgets))


class TuiFrame(urwid.Frame):
    def __init__(self, screen):
        self.screen = screen
        self.render_cols_rows = self.screen.get_cols_rows()
        self.column_needs = (18, 40, 62)
        self.history = []
        self.app_bridge = None

        self.left = ContextList(self, test_contexts)
        self.right = urwid.Filler(urwid.Text(
            [ENVELOPES], 'center'),
            valign='middle')
        self.middle = urwid.Filler(urwid.Text(
            [HELLO, ('subtle', HELLO_CREDITS)], 'center'),
            valign='middle')

        self.default_topbar()
        self.visible_columns = 0
        self.update_columns(update=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)

    async def incoming_message(self, message):
        print('Got message: %s' % message)

    def link_bridge(self, app_bridge):
        self.app_bridge = app_bridge
        return self.incoming_message

    def set_context(self, contexts, i):
        widget = ContextList(self, contexts, expanded=i)
        if self.visible_columns > 1:
            self.left = widget
        else:
            self.history.append((time.time(), 'middle', self.middle))
            self.middle = widget
        self.update_columns()

    def show_tag(self, which):
        self.history.append((time.time(), 'middle', self.middle))
        self.middle = EmailList(self, test_emails)
        self.update_columns()

    def show_email(self, which):
        pass

    def max_child_rows(self):
        return self.screen.get_cols_rows()[1] - 2

    def render(self, *args, **kwargs):
        # This lets us adapt our display to screen width;
        # hiding or showing columns as necessary.
        cols_rows = self.screen.get_cols_rows()
        if self.render_cols_rows != cols_rows:
            self.render_cols_rows = cols_rows
            self.update_columns()
        return urwid.Frame.render(self, *args, **kwargs)

    def default_topbar(self):
        self.topbar = urwid.Pile([
            urwid.AttrMap(urwid.Columns([
                urwid.Text(' %s v%s ' % (APPNAME, APPVER), align='left'),
                urwid.Text([
                         ('top_hk', '/:'), 'Search  ',
                         ('top_hk', '?:'), 'Help  ',
                         ('top_hk', 'q:'), 'Quit '],
                    align='right')]), 'header'),
           urwid.AttrMap(urwid.Text(''), 'crumbs')])

    def update_columns(self, update=True):
        cols, rows = self.screen.get_cols_rows()

        def _b(w):
            if hasattr(w, 'rows'):
                return w
            return urwid.BoxAdapter(w, rows-2)

        columns = []
        if cols > sum(self.column_needs[:2])+1:
          columns.append(
            ('fixed', self.column_needs[0], _w(_b(self.left), 'sidebar')))
        columns.append(
            ('weight', self.column_needs[1], _w(_b(self.middle), 'content')))
        if cols >= sum(self.column_needs)+2:
          columns.append(
            ('weight', self.column_needs[2], _w(_b(self.right), 'sidebar')))

        self.visible_columns = len(columns)
        self.columns = urwid.Columns(columns, dividechars=1)
        if update:
            self.contents['body'] = (self.columns, None)

    def unhandled_input(self, key):
        if key == 'q':
            raise urwid.ExitMainLoop()


def Main(workdir, args):
    app_worker = AppWorker(workdir).connect()
    aev_loop = asyncio.get_event_loop()
    app_bridge = None
    try:
        tui_palette = [
            (None,             'light gray',  'black',     ''),
            ('',               'light gray',  'black',     ''),
            ('body',           'light gray',  'black',     ''),
            ('sidebar',        'light gray',  'black',     ''),
            ('content',        'light gray',  'black',     ''),
            ('email',          'brown',       'black',     ''),
            ('active',         'light blue',  'black',     ''),
            ('active',         'white',       'brown',     ''),
            ('hotkey',         'brown',       'black',     ''),
            ('act_hk',         'black',       'brown',     ''),
            ('crumbs',         'white',       'dark blue', ''),
            ('header',         'light gray',  'black',     ''),
            ('top_hk',         'brown',       'black',     ''),
            ('subtle',         'dark gray',   'black',     ''),
            ('email_from',     'light gray',  'black',     ''),
            ('email_attrs',    'dark gray',   'black',     ''),
            ('email_subject',  'light gray',  'black',     ''),
            ('email_date',     'dark gray',   'black',     ''),
            ('focus',          'white',       'dark blue', '')]

        screen = urwid.raw_display.Screen()
        tui_frame = TuiFrame(screen)
        app_bridge = AsyncRPCBridge(aev_loop, app_worker, tui_frame)

        urwid.MainLoop(urwid.AttrMap(tui_frame, 'body'), tui_palette,
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
        if app_worker.is_alive():
            app_worker.quit()
