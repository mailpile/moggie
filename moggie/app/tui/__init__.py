import asyncio
import copy
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

from ...config import AppConfig, APPNAME, APPVER
from ...email.metadata import Metadata
from ...email.addresses import AddressInfo
from ...jmap.core import JMAPSessionResource
from ...jmap.requests import *
from ...util.rpc import AsyncRPCBridge
from ...workers.app import AppWorker
from ..suggestions import *
from .decorations import palette, ENVELOPES, HELLO, HELLO_CREDITS


def dbg(txt):
    sys.stderr.write(str(txt) + '\n')


def _w(w, attr={}, valign='top'):
    return urwid.AttrWrap(urwid.Filler(w, valign=valign), attr)


class Selectable(urwid.WidgetWrap):
    def __init__(self, contents, on_select=None):
        self.contents = contents
        self.on_select = on_select or {}
        self._focusable = urwid.AttrMap(self.contents, '', dict(
            ((a, 'focus') for a in [None,
                'email', 'subtle', 'hotkey', 'active', 'act_hk',
                'list_from', 'list_attrs', 'list_subject', 'list_date',
                'check_from', 'check_attrs', 'check_subject', 'check_date'])))
        super(Selectable, self).__init__(self._focusable)

    def selectable(self):
        return True

    def keypress(self, size, key):
        if key in self.on_select:
            self.on_select[key](self)
        else:
            return key


class CloseButton(Selectable):
    PLACEHOLDER = urwid.Text('   ')
    def __init__(self, on_select=None):
        Selectable.__init__(self, urwid.Text(('subtle', '[x]')),
            on_select={'enter': on_select})


class QuestionDialog(urwid.WidgetWrap):
    WANTED_HEIGHT = 4
    WANTED_WIDTH = 40
    signals = ['close']
    def __init__(self):
        close_button = urwid.Button(('subtle', '[x]'))
        urwid.connect_signal(close_button, 'click', lambda b: self._emit('close'))
        fill = urwid.Filler(urwid.Pile([
            urwid.Text('WTF OMG LOL'),
            close_button]))
        super().__init__(urwid.AttrWrap(fill, 'popbg'))


class SearchDialog(urwid.WidgetWrap):
    HELP_TEXT = """\

Examples:
 - in:inbox is:unread
 - from:joe has:attachment
 - dates:2010-01..2010-04
 - party +from:mom -to:dad
 - h* *orld

Note: Multiple terms will narrow the search, unless
prefixed with a + to "add" or - to "remove" hits.
Use an asterisk (*) to search for word fragments.
"""
    WANTED_HEIGHT = 6 + len(HELP_TEXT.splitlines())
    WANTED_WIDTH = 60

    signals = ['close']

    def search(self, terms):
        if '\n' in terms:
            terms = terms.replace('\n', '').strip()
            if not self.exact.get_state():
                def _fuzz(term):
                    if ':' in term or '*' in term or term[:1] in ('-', '+'):
                        return term
                    if term[-1:] == 's':
                        term = term[:-1]
                    return term + '*'
                terms = ' '.join(_fuzz(w) for w in terms.split(' ') if w)
            if terms:
                self.tui_frame.show_search_result(terms)
            self._emit('close')
        elif '/' in terms:
            self._emit('close')

    def __init__(self, tui_frame):
        self.tui_frame = tui_frame
        close_button = CloseButton(on_select=lambda b: self._emit('close'))

        self.exact = urwid.CheckBox('Exact matches only', False)
        self.search_box = urwid.Edit('Search: ',
            multiline=True, allow_tab=False, wrap='clip')
        urwid.connect_signal(
            self.search_box, 'change', lambda b,t: self.search(t))

        fill = urwid.Filler(urwid.Pile([
            self.search_box,
            urwid.Divider(),
            self.exact,
            urwid.Text(('popsubtle', self.HELP_TEXT))]))
        super().__init__(urwid.LineBox(urwid.AttrWrap(fill, 'popbg')))


class SuggestionBox(urwid.Pile):
    DISMISSED = set()

    def __init__(self, fallbacks=None, suggestions=None):
        self.widgets = []
        urwid.Pile.__init__(self, self.widgets)

        self.fallbacks = fallbacks or []
        self.suggestions = suggestions or []
        self.update_suggestions(self.get_suggestions())

    def get_suggestions(self):
        # FIXME: Queue a request for a list of suggestions from
        #        the backend.
        # FIXME: This should be context dependent.
        suggest = copy.copy(self.suggestions)
        for _id in sorted(SUGGESTIONS.keys()):
            if _id in SuggestionBox.DISMISSED:
                continue
            sg_obj = SUGGESTIONS[_id].If_Wanted(None, None)
            if sg_obj is not None:
                suggest.append(sg_obj)
                if len(suggest) >= 3:
                    break
        if not len(suggest):
            suggest.extend(self.fallbacks)
        return suggest

    def set_suggestions(self, suggestions):
        # FIXME: this is dumb
        self.suggestions = suggestions
        self.update_suggestions(self.get_suggestions())

    def _on_activate(self, suggestion):
        def activate(i):
            act = suggestion.action()  # FIXME
            if isinstance(act, RequestBase):
                pass  # FIXME: Send this request to the backend
        return activate

    def _on_dismiss(self, suggestion):
        def dismiss(i):
            SuggestionBox.DISMISSED.add(suggestion.ID)
            self.update_suggestions(self.get_suggestions())
        return dismiss

    def update_suggestions(self, suggest):
        widgets = []
        for sgn in suggest:
            columns = [
                ('fixed',  4, urwid.Text(('subtle', '*'), 'right')),
                ('weight', 1, Selectable(urwid.Text(sgn.message()),
                    on_select={'enter': self._on_activate(sgn)}))]
            if sgn.ID is not None:
                columns.append(
                    ('fixed',  3, CloseButton(
                        on_select=self._on_dismiss(sgn))))
            else:
                columns.append(('fixed',  3, CloseButton.PLACEHOLDER))
            widgets.append(urwid.Columns(columns, dividechars=1))

        self.widgets = widgets
        self.contents = [(w, ('pack', None)) for w in self.widgets]

    def __len__(self):
        return len(self.widgets)

    def incoming_message(self, message):
        pass  # FIXME: Listen for suggestions


class SplashCat(urwid.Filler):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    def __init__(self, message=''):
        self.suggestions = SuggestionBox(fallbacks=[SuggestionWelcome])
        widgets = [
            ('weight', 3, urwid.Text(
                [message, '\n', HELLO, ('subtle', HELLO_CREDITS), '\n'],
                'center'))]
        if len(self.suggestions):
            widgets.append(('pack',  self.suggestions))
        urwid.Filler.__init__(self, urwid.Pile(widgets), valign='middle')

    def incoming_message(self, message):
        self.suggestions.incoming_message(message)


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

    TAG_ITEMS = [
        ('inbox',  (('i', 'INBOX',    'in:inbox'),
                    ('c', 'Calendar', ''),
                    ('p', 'People',   ''),
                    ('a', 'All Mail', 'all:mail'))),
        ('outbox', (('9', 'OUTBOX',   'in:outbox'),)),
        ('sent',   (('0', 'Sent',     'in:sent'),)),
        ('spam',   (('s', 'Spam',     'in:spam'),)),
        ('trash',  (('d', 'Trash',    'in:trash'),))]
    TAG_KEYS = 'wertyu'

    def __init__(self, tui_frame, contexts, expanded=0):
        self.expanded = expanded
        self.tui_frame = tui_frame
        self.crumb = 'ohai'
        self.active = None
        self.walker = urwid.SimpleListWalker([])
        urwid.ListBox.__init__(self, self.walker)

        self.waiting = True
        self.search_obj = RequestContexts()
        self.counts_obj = RequestCounts()

        self.contexts = contexts
        self.tag_counts = {}
        self.update_content()

    def update_content(self):
        def _sel_ctx(which):
            return lambda x: self.tui_frame.set_context(self.contexts, which)
        def _sel_email(which):
            return lambda x: self.tui_frame.show_account(self.active, which)
        def _sel_search(terms):
            return lambda x: self.tui_frame.show_search_result(terms)

        widgets = []
        last_ctx_name = '-:-!-:-'
        self.contexts.sort(
            key=lambda c: (0 if c['key'] == 'Context 0' else 1, c['name']))
        for i, ctx in enumerate(self.contexts):

            name = ctx['name']
            if name.startswith(last_ctx_name+' '):
                name = ' - ' + name[len(last_ctx_name)+1:]
            else:
                last_ctx_name = name

#           sc = ('g%d:' % (i+1)) if (i < 8) else '   '
            ctx_name = urwid.Text([
#               ('hotkey', sc),
                ('subtle', name)], 'left', 'clip')

            if i == self.expanded:
                last_ctx_name = name
                #last_ctx_name = '-:-!-:-'
                self.active = ctx['key']
                self.crumb = ctx['name']
                widgets.append(Selectable(urwid.AttrMap(ctx_name,
                    {None: 'active', 'subtle': 'active', 'hotkey': 'act_hk'}),
                    on_select={'enter': self.show_overview}))
                widgets.append(Selectable(urwid.Text(
                    [('subtle', 'live:1')], 'right', 'clip'),
                    on_select={'enter': self.show_connections}))
            else:
                widgets.append(Selectable(ctx_name,
                    on_select={'enter': _sel_ctx(i)}))

            if i == self.expanded:
                acount = 0
                for akey, acct in sorted(ctx.get('accounts', {}).items()):
                    widgets.append(Selectable(urwid.Padding(
                            urwid.Text(('email', acct['name']), 'left', 'clip'),
                            left=1, right=1),
                        on_select={}))  # FIXME
                    acount += 1
                    if acount > 3:
                        pass  # FIXME: Add a "more" link, break loop
                if acount:
                    widgets.append(urwid.Divider())

                shown = []
                for tag, items in self.TAG_ITEMS:
                    if tag in ctx.get('tags', []):
                        for sc, name, search in items:
                            sc = (' %s:' % sc) if sc else '   '
                            os = search and {'enter': _sel_search(search)}
                            widgets.append(Selectable(
                                urwid.Text([('hotkey', sc), name]),
                                on_select=os))
                        shown.append(tag)
                count = 1
                unshown = [t for t in ctx.get('tags', []) if t not in shown]
                if unshown:
                    widgets.append(urwid.Divider())
                    for tag in unshown:
                        sc = ''
                        if count <= len(self.TAG_KEYS):
                            sc = (' %s:' % self.TAG_KEYS[count-1])
                        count += 1
                        name = tag[:1].upper() + tag[1:]
                        widgets.append(Selectable(
                            urwid.Text([('hotkey', sc), name]),
                            on_select={'enter': _sel_search('in:%s' % tag)}))
                    if count > 5:
                        pass  # FIXME: Add a "more" link, break loop
                widgets.append(urwid.Divider())

        if len(widgets) == 0:
            widgets.append(urwid.Text('\n(unconfigured) \n', 'center'))

        widgets.append(urwid.Text([('subtle', '_'*20)], 'left', 'clip'))
        widgets.append(Selectable(urwid.Text(
                [('hotkey', 'C:'), ('subtle', 'add context')], 'right'),
            on_select={'enter': lambda x: None}))

        self.walker[0:] = widgets

    def show_overview(self, i=None):
        pass  # FIXME

    def show_connections(self, i=None):
        pass  # FIXME

    def request_counts(self):
        self.counts_obj['terms_list'] = count_terms = []
        for i, ctx in enumerate(self.contexts):
            if i == self.expanded:
                for tag in ctx.get('tags', []):
                    count_terms.append('in:%s' % tag)
                    count_terms.append('in:%s is:unread' % tag)
        # FIXME: What about contexts?
        self.tui_frame.app_bridge.send_json(self.counts_obj)

    def incoming_message(self, message):
        if self.waiting:
            self.tui_frame.app_bridge.send_json(self.search_obj)
            self.waiting = False
        if (message.get('prototype') == self.search_obj['prototype']):
            self.contexts = message['contexts']
            self.update_content()
            self.request_counts()
        elif (message.get('prototype') == self.counts_obj['prototype']):
            self.update_content()

        # FIXME: The backend should broadcast updates...


class EmailListWalker(urwid.ListWalker):
    def __init__(self, parent):
        self.focus = 0
        self.emails = []
        self.selected = set()
        self.selected_all = False
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
            md = Metadata(*self.emails[pos])
            uuid = md.uuid
            md = md.parsed()
            dt = datetime.datetime.fromtimestamp(md.get('ts', 0))
            if self.selected_all or uuid in self.selected:
                prefix = 'check'
                attrs = '>    <'
                dt = dt.strftime('%Y-%m  ✓')
            else:
                attrs = '(    )'
                prefix = 'list'
                dt = dt.strftime('%Y-%m-%d')
            frm = md.get('from', {})
            frm = frm.get('fn') or frm.get('address') or '(none)'
            subj = md.get('subject', '(no subject)')
            cols = urwid.Columns([
              ('weight', 15, urwid.Text((prefix+'_from', frm), wrap='clip')),
              (6,            urwid.Text((prefix+'_attrs', attrs))),
              ('weight', 27, urwid.Text((prefix+'_subject', subj), wrap='clip')),
              (10,           urwid.Text((prefix+'_date', dt), align='left'))],
              dividechars=1)
            return Selectable(cols, on_select={
                'enter': lambda x: self.parent.show_email(self.emails[pos]),
                'x': lambda x: self.check(uuid),
                ' ': lambda x: self.check(uuid, display=self.emails[pos])})
        except IndexError:
            pass
        except:
            dbg(traceback.format_exc())
        raise IndexError

    def check(self, uuid, display=None):
        had_any = (len(self.selected) > 0)
        if uuid in self.selected and not display:
            self.selected.remove(uuid)
        else:
            self.selected.add(uuid)
        have_any = (len(self.selected) > 0)

        # Warn the container that our selection state has changed.
        if had_any != have_any:
            self.parent.update_content()

        self._modified()
        # FIXME: There must be a better way to do this...
        self.parent.keypress((100,), 'down')
        if display is not None:
            self.parent.show_email(display)


class SuggestAddToIndex(Suggestion):
    MESSAGE = 'Add these messages to the search index'

    def __init__(self, app_bridge, context, search_obj):
        Suggestion.__init__(self, context, None)  # FIXME: Config?
        self.app_bridge = app_bridge
        self.request_add = RequestAddToIndex(
            context=context,
            search=search_obj)
        self._message = self.MESSAGE
        self.adding = False

    def action(self):
        self.app_bridge.send_json(self.request_add)
        self.adding = True

    def message(self):
        # FIXME: If updates are happening, turn into a progress
        #        reporting message?
        if self.adding:
            return 'ADDING, WOOO'
        return self._message


class EmailList(urwid.Pile):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, search_obj):
        self.search_obj = search_obj
        self.tui_frame = tui_frame
        self.app_bridge = tui_frame.app_bridge

        self.crumb = search_obj.get('mailbox', 'FIXME')
        self.global_hks = {
            'J': [lambda *a: None, ('top_hk', 'J:'), 'Read Next '],
            'K': [lambda *a: None, ('top_hk', 'K:'), 'Previous  ']}

        self.column_hks = [('top_hk', 'A:'), 'Add To Index']

        self.walker = EmailListWalker(self)
        self.emails = self.walker.emails
        self.listbox = urwid.ListBox(self.walker)
        self.suggestions = SuggestionBox()
        self.widgets = []

        self.loading = 0
        self.want_more = True
        self.load_more()

        urwid.Pile.__init__(self, [])
        self.update_content()

    def update_content(self):
        self.widgets[0:] = []
        rows = self.tui_frame.max_child_rows()

        if not self.emails:
            message = 'Loading ...' if self.loading else 'No mail here!'
            cat = urwid.BoxAdapter(SplashCat(message), rows)
            self.contents = [(cat, ('pack', None))]
            return
        elif self.search_obj['prototype'] != 'search':
            self.suggestions.set_suggestions([
                SuggestAddToIndex(
                    self.app_bridge,
                    self.tui_frame.current_context,
                    self.search_obj)])

        # Inject suggestions above the list of messages, if any are
        # present. This can change dynamically as the backend sends us
        # hints.
        if self.walker.selected:
            self.widgets.append(urwid.Columns([
                ('weight', 1, urwid.Text(
                    'NOTE: You are operating directly on a mailbox!\n'
                    '      Tagging will add emails to the search index.\n'
                    '      Deletion cannot be undone.')),
                ('fixed', 3, CloseButton(None))]))
        elif len(self.suggestions):
            self.widgets.append(self.suggestions)

        rows -= sum(w.rows((60,)) for w in self.widgets)
        if self.widgets:
            self.widgets.append(urwid.Divider())
            rows -= 1
        self.widgets.append(urwid.BoxAdapter(self.listbox, rows))

        self.contents = [(w, ('pack', None)) for w in self.widgets]

    def cleanup(self):
        del self.tui_frame
        del self.app_bridge
        del self.walker.emails
        del self.walker
        del self.emails
        del self.search_obj
        del self.listbox
        del self.widgets

    def show_email(self, metadata):
        self.tui_frame.col_show(self, EmailDisplay(self.tui_frame, metadata))
        try:
            self.tui_frame.columns.set_focus_path([1])
        except IndexError:
            pass

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
        self.suggestions.incoming_message(message)
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
        self.update_content()


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
        for field in ('Date:', 'To:', 'Cc:', 'From:', 'Reply-To:', 'Subject:'):
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
        yield(urwid.Divider())

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


class PopUpManager(urwid.PopUpLauncher):
    def __init__(self, tui_frame, content):
        super().__init__(content)
        self.tui_frame = tui_frame
        self.target = SearchDialog
        self.target_args = []

    def create_pop_up(self):
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
    def __init__(self, screen):
        self.screen = screen
        self.render_cols_rows = self.screen.get_cols_rows()
        self.app_bridge = None

        self.filler1 = SplashCat('Welcome to Moggie!')
        self.filler2 = SplashMoreWide()
        self.filler3 = SplashMoreNarrow()

        self.hidden = 0
        self.crumbs = []
        self.columns = urwid.Columns([self.filler1], dividechars=1)
        self.context_list = ContextList(self, [])
        self.all_columns = [self.context_list]
        self.update_topbar(update=False)
        self.update_columns(update=False, focus=False)

        urwid.Frame.__init__(self, self.columns, header=self.topbar)

    current_context = property(lambda s: s.context_list.active)

    def incoming_message(self, message):
        message = json.loads(message)
        for widget in self.all_columns:
            if hasattr(widget, 'incoming_message'):
                try:
                    widget.incoming_message(message)
                except:
                    dbg(traceback.format_exc())

    def link_bridge(self, app_bridge):
        self.app_bridge = app_bridge
        return self.incoming_message

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

    def update_topbar(self, update=True):
        # FIXME: Calculate/hint hotkeys based on what our columns suggest?

        maxwidth = self.render_cols_rows[0] - 2
        crumbtrail = ' -> '.join(self.crumbs)
        if len(crumbtrail) > maxwidth:
            crumbtrail = '...' + crumbtrail[-(maxwidth-3):]

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

        mv = ' %s v%s ' % (APPNAME, APPVER)
        self.topbar = PopUpManager(self, urwid.Pile([
            urwid.AttrMap(urwid.Columns([
                ('fixed', len(mv), urwid.Text(mv, align='left')),
                ('weight', 1, urwid.Text(
                    global_hks + [
                        ('top_hk', '/:'), 'Search ',
                        ('top_hk', '?:'), 'Help ',
                        ('top_hk', 'q:'), 'Quit '],
                    align='right', wrap='clip'))]), 'header'),
            urwid.AttrMap(urwid.Columns([
                urwid.Text(crumbtrail, align='left'),
                ]), 'crumbs')]))
        if update:
            self.contents['header'] = (self.topbar, None)

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
                raise urwid.ExitMainLoop()
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
                self.topbar.open_pop_up()
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
            #
            # FIXME: incomplete, we need to also ensure that Context Zero
            # is selected. Is setting expanded=0 reliably that?
            tui_frame.show_mailbox(tui_args['-f'], AppConfig.CONTEXT_ZERO)
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
