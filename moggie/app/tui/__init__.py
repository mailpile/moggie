import asyncio
import datetime
import json
import re
import random
import sys
import time
import urwid
import traceback

import websockets
import websockets.exceptions

from ...config import APPNAME, APPVER
from ...email.metadata import Metadata
from ...email.addresses import AddressInfo
from ...jmap.core import JMAPSessionResource
from ...jmap.requests import *
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker
from ..core import test_contexts, test_emails
from .decorations import palette, ENVELOPES, HELLO, HELLO_CREDITS


def dbg(txt):
    sys.stderr.write(str(txt) + '\n')


def _w(w, attr={}, valign='top'):
    return urwid.AttrWrap(urwid.Filler(w, valign=valign), attr)


class Selectable(urwid.WidgetWrap):
    def __init__(self, contents, on_select=None):
        self.contents = contents
        self.on_select = on_select
        self._focusable = urwid.AttrMap(self.contents, '', dict(
            ((a, 'focus') for a in [None,
                'email', 'subtle', 'hotkey', 'active', 'act_hk',
                'list_from', 'list_attrs', 'list_subject', 'list_date'])))
        super(Selectable, self).__init__(self._focusable)

    def selectable(self):
        return True

    def keypress(self, size, key):
        if self.on_select and key in ('enter',):
            self.on_select(self)
        else:
            return key


class SplashCat(urwid.Filler):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    def __init__(self):
        urwid.Filler.__init__(self,
            urwid.Text([HELLO, ('subtle', HELLO_CREDITS)], 'center'),
            valign='middle')


class SplashMoreWide(urwid.Filler):
    COLUMN_NEEDS = 60
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    CONTENT = ENVELOPES + '\n\n\n\n'
    def __init__(self):
        urwid.Filler.__init__(self,
            urwid.Text([self.CONTENT], 'center'),
            valign='middle')


class SplashMoreNarrow(SplashMoreWide):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 40
    CONTENT = '\n\n\n\n' + ENVELOPES


class ContextList(urwid.ListBox):
    COLUMN_NEEDS = 18
    COLUMN_WANTS = 18
    COLUMN_FIT = 'fixed'
    COLUMN_STYLE = 'sidebar'

    def __init__(self, tui_frame, contexts, expanded=0):
        self.contexts = contexts
        self.expanded = expanded
        self.tui_frame = tui_frame
        self.crumb = 'ohai'

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
                self.crumb = ctx['name']
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

    def incoming_message(self, message):
        pass


class EmailListWalker(urwid.ListWalker):
    def __init__(self, parent):
        self.focus = 0
        self.emails = []
        self.parent = parent

    def __len__(self):
        return len(self.emails)

    def add_emails(self, skip, emails):
        self.emails[skip:] = emails
        self.emails.sort()
        self.emails.reverse()
        self._modified()

    def set_focus(self, focus):
        self.focus = focus
        if focus > len(self.emails) - 100:
            self.parent.load_more()

    def next_position(self, pos):
        if pos + 1 < len(self.emails):
            return pos + 1
        self.parent.load_more()
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
              ('weight', 15, urwid.Text(('list_from', frm), wrap='clip')),
              (6,            urwid.Text(('list_attrs', attrs))),
              ('weight', 27, urwid.Text(('list_subject', subj), wrap='clip')),
              (10,           urwid.Text(('list_date', dt)))],
              dividechars=1)
            return Selectable(cols,
                on_select=lambda x: self.parent.show_email(self.emails[pos]))
        except:
            dbg(traceback.format_exc())
        raise IndexError


class EmailList(urwid.ListBox):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, search_obj):
        self.search_obj = search_obj
        self.tui_frame = tui_frame
        self.app_bridge = tui_frame.app_bridge
        self.crumb = search_obj.get('mailbox', 'FIXME')

        self.walker = EmailListWalker(self)
        self.emails = self.walker.emails
        urwid.ListBox.__init__(self, self.walker)

        self.loading = 0
        self.want_more = True
        self.load_more()

    def cleanup(self):
        del self.tui_frame
        del self.app_bridge
        del self.walker.emails
        del self.walker
        del self.emails
        del self.search_obj

    def show_email(self, metadata):
        self.tui_frame.show(self, EmailDisplay(self.tui_frame, metadata))

    def load_more(self):
        now = time.time()
        if (self.loading > now - 5) or not self.want_more:
            return
        self.loading = time.time()
        self.search_obj.update({
            'skip': len(self.emails),
            'limit': min(max(500, 2*len(self.emails)), 10000)})
        self.app_bridge.send_json(self.search_obj)

    def incoming_message(self, message):
        if (message.get('prototype') != self.search_obj['prototype'] or
                message.get('req_id') != self.search_obj['req_id']):
            return
        try:
            self.walker.add_emails(message['skip'], message['emails'])

            self.want_more = (message['limit'] == len(message['emails']))
            self.loading = 0
            self.load_more()
        except:
            dbg(traceback.format_exc())


class EmailDisplay(urwid.ListBox):
    COLUMN_NEEDS = 60
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, metadata, parsed=None):
        self.tui_frame = tui_frame
        self.metadata = Metadata(*metadata)
        self.parsed = self.metadata.parsed()
        self.email = parsed
        self.uuid = self.metadata.uuid_asc
        self.crumb = self.parsed.get('subject', 'FIXME')

        self.email_body = urwid.Text('(loading...)')
        self.widgets = urwid.SimpleListWalker(
            list(self.headers()) + [self.email_body])

        self.search_obj = RequestEmail(self.metadata, text=True)
        self.tui_frame.app_bridge.send_json(self.search_obj)

        urwid.ListBox.__init__(self, self.widgets)

    def headers(self):
        for field in ('Date:', 'To:', 'Cc:', 'From:', 'Subject:'):
            fkey = field[:-1].lower()
            if fkey not in self.parsed:
                continue

            value = self.parsed[fkey]
            if not isinstance(value, list):
                value = [value]

            for val in value:
                if isinstance(val, AddressInfo):
                    if val.fn:
                        val = '%s <%s>' % (val.fn, val.address)
                    else:
                        val = '<%s>' % val.address
                else:
                    val = str(val).strip()
                if not val:
                    continue
                yield urwid.Columns([
                    ('fixed',  8, urwid.Text(('email_key_'+fkey, field), align='right')),
                    ('weight', 4, urwid.Text(('email_val_'+fkey, val)))],
                    dividechars=1)
                field = ''
        yield(urwid.Text(''))

    def cleanup(self):
        del self.tui_frame
        del self.email

    def incoming_message(self, message):
        if (message.get('prototype') != self.search_obj['prototype'] or
                message.get('req_id') != self.search_obj['req_id']):
            return
        self.email = message['email']

        email_text = ''
        for ctype in ('text/plain', 'text/html'):
            for part in self.email['_PARTS']:
                if part['content-type'][0] == ctype:
                    email_text += part.get('_TEXT', '')
            if email_text:
                break
        email_text = re.sub(r'\n\s*\n', '\n\n', email_text, flags=re.DOTALL)

        self.email_body = urwid.Text(email_text)
        self.widgets[-1] = self.email_body


class TuiFrame(urwid.Frame):
    def __init__(self, screen):
        self.screen = screen
        self.render_cols_rows = self.screen.get_cols_rows()
        self.app_bridge = None

        self.filler1 = SplashCat()
        self.filler2 = SplashMoreWide()
        self.filler3 = SplashMoreNarrow()

        self.hidden = 0
        self.crumbs = []
        self.columns = urwid.Columns([self.filler1], dividechars=1)
        self.all_columns = [ContextList(self, test_contexts)]
        self.update_topbar(update=False)
        self.update_columns(update=False, focus=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)

    def incoming_message(self, message):
        message = json.loads(message)
        for widget in self.all_columns:
            if hasattr(widget, 'incoming_message'):
                widget.incoming_message(message)

    def link_bridge(self, app_bridge):
        self.app_bridge = app_bridge
        return self.incoming_message

    def set_context(self, contexts, i):
        self.all_columns[0] = ContextList(self, contexts, expanded=i)
        self.update_columns()

    def show_tag(self, which):
        self.show(self.all_columns[0], EmailList(self, RequestTag(which)))

    def show_mailbox(self, which):
        self.show(self.all_columns[0], EmailList(self, RequestMailbox(which)))

    def show_search_result(self, which):
        self.show(self.all_columns[0], EmailList(self, RequestSearch(which)))

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

    def update_topbar(self, update=True):
        # FIXME: Calculate/hint hotkeys based on what our columns suggest?

        maxwidth = self.render_cols_rows[0] - 2
        crumbtrail = ' -> '.join(self.crumbs)
        if len(crumbtrail) > maxwidth:
            crumbtrail = '...' + crumbtrail[-(maxwidth-3):]

        self.topbar = urwid.Pile([
            urwid.AttrMap(urwid.Columns([
                urwid.Text(' %s v%s ' % (APPNAME, APPVER), align='left'),
                urwid.Text([
                         ('top_hk', '/:'), 'Search  ',
                         ('top_hk', '?:'), 'Help  ',
                         ('top_hk', 'x:'), 'Close  ',
                         ('top_hk', 'q:'), 'Quit '],
                    align='right')]), 'header'),
            urwid.AttrMap(
                urwid.Text(crumbtrail, align='center'), 'crumbs')])
        if update:
            self.contents['header'] = (self.topbar, None)

    def focus_last_column(self):
        self.columns.set_focus_path([len(self.all_columns) - self.hidden - 1])

    def show(self, ref, widget):
        self.remove(ref, ofs=1, update=False)
        self.all_columns.append(widget)
        self.update_columns(focus=False)
        self.focus_last_column()

    def replace(self, ref, widget):
        self.remove(ref, update=False)
        self.all_columns.append(widget)
        self.update_columns(focus=False)
        self.focus_last_column()

    def remove(self, ref, ofs=0, update=True):
        pos = self.all_columns.index(ref)
        if pos > 0:
            pos += ofs
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
        if key == 'q':
            raise urwid.ExitMainLoop()
        elif key == 'x':
            if len(self.all_columns) > 1:
                self.remove(self.all_columns[-1])
        elif key == 'left':
            if len(self.all_columns) > 1 and self.hidden:
                self.remove(self.all_columns[-1])
        elif key == 'right':
            self.columns.keypress((0,0), 'enter')
        else:
            return key


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

        dbg('APP IS%s LOCKED' % ('' if app_is_locked else ' NOT'))

        if not app_is_locked:
            jsr = JMAPSessionResource(app_worker.call('rpc/jmap_session'))
            dbg(jsr)
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
