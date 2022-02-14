import asyncio
import datetime
import json
import random
import time
import urwid
import traceback

import websockets
import websockets.exceptions

from ...config import APPNAME, APPVER
from ...email.metadata import Metadata
from ...jmap.core import JMAPSessionResource
from ...jmap.requests import *
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker
from ..core import test_contexts, test_emails
from .decorations import palette, ENVELOPES, HELLO, HELLO_CREDITS


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


class EmailDisplay(urwid.Filler):
    def __init__(self, tui_frame, metadata, parsed):
        self.metadata = Metadata(*metadata)
        self.parsed = self.metadata.parsed()
        self.email = parsed

        urwid.Filler.__init__(self, urwid.Text(
            [self.parsed.get('subject', 'ohai')], 'center'),
            valign='middle')


class EmailListWalker(urwid.ListWalker):
    def __init__(self, tui_frame, mailbox, emails=None):
        self.focus = 0
        self.mailbox = mailbox
        self.emails = emails or []
        self.tui_frame = tui_frame
        self.app_bridge = tui_frame.app_bridge
        self.loading = 0
        self.want_more = len(self.emails) < 100
        self._load_more()

    def __len__(self):
        return len(self.emails)

    def _load_more(self):
        now = time.time()
        if (self.loading > now - 5) or not self.want_more:
            return
        self.loading = time.time()
        self.app_bridge.send_json(RequestMailbox(self.mailbox,
            skip=len(self.emails),
            limit=min(max(500, 2*len(self.emails)), 10000)))

    def incoming_message(self, message):
        if (message.get('prototype') != 'mailbox' or
                message.get('mailbox') != self.mailbox):
            return

        try:
            self.emails[message['skip']:] = message['emails']
            self.emails.sort()
            self.emails.reverse()

            self.want_more = (message['limit'] == len(message['emails']))
            self.loading = 0
            self._load_more()

            self._modified()
        except:
            traceback.print_exc()

    def set_focus(self, focus):
        self.focus = focus
        if focus > len(self.emails) - 100:
            self._load_more()

    def next_position(self, pos):
        if pos + 1 < len(self.emails):
            return pos + 1
        self._load_more()
        raise IndexError

    def prev_position(self, pos):
        if pos > 0:
            return pos - 1
        raise IndexError

    def positions(self, reverse=False):
        if reverse:
            return reversed(range(0, len(self.emails)))
        return range(0, len(self.emails))

    def __getitem__(self, pos):
        try:
            md = Metadata(*self.emails[pos]).parsed()
            frm = md.get('from', {})
            frm = frm.get('fn') or frm.get('address') or '(none)'
            attrs = '(    )'
            subj = md.get('subject', '(no subject)')
            dt = datetime.datetime.fromtimestamp(md.get('ts', 0))
            dt = dt.strftime('%Y-%m-%d')
            cols = urwid.Columns([
              ('weight', 15, urwid.Text(('email_from', frm), wrap='clip')),
              (6,            urwid.Text(('email_attrs', attrs))),
              ('weight', 27, urwid.Text(('email_subject', subj), wrap='clip')),
              (10,           urwid.Text(('email_date', dt)))],
              dividechars=1)
            return Selectable(cols,
                on_select=lambda x: self.tui_frame.show_email(self.emails[pos]))
        except:
            traceback.print_exc()
        raise IndexError


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
        self.middle = urwid.Filler(urwid.Text(
            [HELLO, ('subtle', HELLO_CREDITS)], 'center'),
            valign='middle')
        self.right = urwid.Filler(urwid.Text(
            [ENVELOPES], 'center'),
            valign='middle')

        self.default_topbar()
        self.visible_columns = 0
        self.swap_visible = False
        self.update_columns(update=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)

    def incoming_message(self, message):
        message = json.loads(message)
        for _, _, _, widget in self.history:
            if widget is not None:
                widget.incoming_message(message)

    def link_bridge(self, app_bridge):
        self.app_bridge = app_bridge
        return self.incoming_message

    def set_context(self, contexts, i):
        widget = ContextList(self, contexts, expanded=i)
        if self.visible_columns > 1:
            self.left = widget
        else:
            self.history.append((time.time(), 'middle', self.middle, None))
            self.middle = widget
        self.update_columns()

    def show_tag(self, which):
        elw = EmailListWalker(self, None, test_emails)  # FIXME
        self.history.append((time.time(), 'middle', self.middle, elw))
        #self.app_bridge.send_json(RequestTag(which))
        self.middle = urwid.ListBox(elw)
        self.update_columns()

    def show_mailbox(self, which):
        elw = EmailListWalker(self, which)
        self.history.append((time.time(), 'middle', self.middle, elw))
        self.middle = urwid.ListBox(elw)
        self.update_columns()

    def show_search_result(self, which):
        elw = EmailListWalker(self, None, test_emails)  # FIXME
        self.history.append((time.time(), 'middle', self.middle, elw))
        #self.app_bridge.send_json(RequestSearch(which))
        self.middle = urwid.ListBox(elw)
        self.update_columns()

    def show_email(self, metadata):
        edw = EmailDisplay(self, metadata, None)
        self.history.append((time.time(), 'right', self.right, edw))
        self.app_bridge.send_json(RequestEmail(metadata, text=True))
        self.right = edw
        self.update_columns()

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
        if cols >= sum(self.column_needs)+2:
          columns.append(
            ('weight', self.column_needs[1], _w(_b(self.middle), 'content')))
          columns.append(
            ('weight', self.column_needs[2], _w(_b(self.right), 'sidebar')))
        else:
          if self.swap_visible:
            columns.append(
              ('weight', self.column_needs[2], _w(_b(self.right), 'content')))
          else:
            columns.append(
              ('weight', self.column_needs[1], _w(_b(self.middle), 'content')))

        self.visible_columns = len(columns)
        self.columns = urwid.Columns(columns, dividechars=1)
        if update:
            self.contents['body'] = (self.columns, None)

    def unhandled_input(self, key):
        if key == 'q':
            raise urwid.ExitMainLoop()
        if key == 'right':
            self.swap_visible = not self.swap_visible
        self.update_columns()


def Main(workdir, sys_args, tui_args, send_args):
    app_bridge = app_worker = None
    try:
        app_worker = AppWorker(workdir).connect()
        screen = urwid.raw_display.Screen()
        tui_frame = TuiFrame(screen)
        aev_loop = asyncio.get_event_loop()
        app_bridge = AsyncRPCBridge(aev_loop, app_worker, tui_frame)

        # Request "locked" status from the app.
        app_crypto_status = app_worker.call('rpc/crypto_status')
        app_is_locked = app_crypto_status.get('locked')

        print('APP IS%s LOCKED' % ('' if app_is_locked else ' NOT'))

        if not app_is_locked:
            jsr = JMAPSessionResource(app_worker.call('rpc/jmap_session'))
            print(jsr)
            # Request list of available JMAP Sessions from the app.
            # Establish a websocket/JMAP connection to each Session.
            # Populate sidebar.
            pass  # FIXME

        if send_args['_order']:
            # Display the composer
            # (Note, if locked, then "send" will just queue the messasge)
            pass  # FIXME

        elif '-f' in tui_args:
            # Display the contents of a mailbox; this should always be
            # possible whether app is locked or not.
            tui_frame.show_mailbox(tui_args['-f'])

        elif not app_is_locked:
            # Display default Session/INBOX
            tui_frame.show_tag('inbox')  # FIXME

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
