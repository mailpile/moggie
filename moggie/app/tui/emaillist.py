# TODO:
#   - Bring back "Add to the index" when viewing a mailbox (m2)
#   - Add the ability to export selected messages to a file (m2)
#   - Allow the user to toggle between message and thread views
#   - Add "search refiners" shortcuts to further narrow a search
#   - Make tagging/untagging work!
#   - Experiment with a twitter-like people-centric view?
#
import datetime
import logging
import re
import sys
import time
import urwid

from ...email.metadata import Metadata
from ...api.requests import RequestAddToIndex, RequestCommand
from ..suggestions import Suggestion

from .suggestionbox import SuggestionBox
from .emaildisplay import EmailDisplay
from .widgets import *


class EmailListWalker(urwid.ListWalker):
    SUBJECT_BRACKETS = re.compile(r'^(\[[^\]]{5})[^\] ]{3,}(\])')
    SUBJECT_RE = re.compile(
        r'^((antw|aw|odp|ref?|sv|vs):\s*)*', flags=re.I)

    def __init__(self, parent):
        self.focus = 0
        self.idx = {}
        self.emails = []
        self.visible = []
        self.expanded = set()
        self.selected = set()
        self.selected_all = False
        self.parent = parent

    def __len__(self):
        return len(self.visible)

    def expand(self, msg):
        self.expanded.add(msg['thread_id'])
        self.set_emails(self.emails)

    def set_emails(self, emails, focus_uuid=None):
        self.emails[:] = [e for e in emails if isinstance(e, dict)]
        self.idx = dict((e['idx'], i) for i, e in enumerate(self.emails))

        if self.visible and focus_uuid is None:
            focus_uuid = self.visible[self.focus]['uuid']

        self.visible = [e for e in self.emails
            if e.get('is_hit', True)
            or (e['thread_id'] == e['idx'])
            or (e['thread_id'] in self.expanded)]

        def _thread_first(msg):
            i = self.idx.get(msg['thread_id'], self.idx[msg['idx']])
            return self.emails[i]

        def _depth(msg):
            if msg['idx'] == msg['parent_id']:
                return 0
            i = self.idx.get(msg['parent_id'])
            if i is None:
                return 0
            return 1 + _depth(self.emails[i])

        # This is magic that lets us sort by "reverse thread date, but
        # forward date within thread", as well as indenting the subjects
        # to show the relative position.
        for msg in self.visible:
            tf = _thread_first(msg)
            tf['_tts'] = max(tf.get('_tts') or 0, msg['ts'])
            depth = _depth(msg)
            if depth > 8:
                prefix = '  %d> ' % depth
                prefix += ' ' * (9 - len(prefix))
            else:
                prefix = ' ' * depth
            msg['_prefix'] = prefix

        def _sort_key(msg):
            return (-_thread_first(msg)['_tts'], msg['ts'], msg['idx'])

        self.visible.sort(key=_sort_key)

        # Keep the focus in the right place!
        if focus_uuid is not None:
            for i, e in enumerate(self.visible):
                if e['uuid'] == focus_uuid:
                    self.focus = i

        self._modified()

    def set_focus(self, focus):
        self.focus = focus
        if focus > len(self.visible) - 50:
            self.parent.load_more()

    def next_position(self, pos):
        if pos + 1 < len(self.visible):
            return pos + 1
        self.parent.load_more()
        raise IndexError

    def prev_position(self, pos):
        if pos > 0:
            return pos - 1
        raise IndexError

    def positions(self, reverse=False):
        if reverse:
            return reversed(range(0, len(self.visible)))
        return range(0, len(self.visible))

    def __getitem__(self, pos):
        def _thread_subject(md, frm):
            subj = md.get('subject',
                '(no subject)' if frm else '(missing message)').strip()
            subj = (
                self.SUBJECT_BRACKETS.sub('\\1..\\2',
                self.SUBJECT_RE.sub('', subj)))
            return md.get('_prefix', '') + subj
        try:
            focused = (pos == self.focus)
            md = self.visible[pos]
            if isinstance(md, list):
                md = Metadata(*md).parsed()

            uuid = md['uuid']
            dt = md.get('ts') or md.get('_tts')
            dt = datetime.datetime.fromtimestamp(dt) if dt else 0
            if self.selected_all or uuid in self.selected:
                prefix = 'check'
                attrs = '>    <'
                dt = dt.strftime('%Y-%m  ✓') if dt else ''
            else:
                attrs = '(    )'
                prefix = 'list'
                fmt = '%Y-%m-%d' if focused else '%Y-%m-%d'
                dt = dt.strftime(fmt) if dt else ''
            frm = md.get('from', {})
            frm = frm.get('fn') or frm.get('address') or ''
            subj = _thread_subject(md, frm)
            wrap = 'clip'
            widget = cols = urwid.Columns([
              ('weight', 15, urwid.Text((prefix+'_from', frm), wrap=wrap)),
              (6,            urwid.Text((prefix+'_attrs', attrs))),
              ('weight', 27, urwid.Text((prefix+'_subject', subj), wrap=wrap)),
              (10,           urwid.Text((prefix+'_date', dt), align='right'))],
              dividechars=1)
            return Selectable(widget, on_select={
                'enter': lambda x: self.parent.show_email(self.visible[pos]),
                'x': lambda x: self.check(uuid),
                ' ': lambda x: self.check(uuid, display=self.visible[pos])})
        except IndexError:
            logging.exception('Failed to load message')
            pass
        except:
            logging.exception('Failed to load message')
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

    def __init__(self, tui_frame, search_obj, ctx_src_id):
        Suggestion.__init__(self, context, None)  # FIXME: Config?
        self.tui_frame = tui_frame
        self.ctx_src_id = ctx_src_id
        self.request_add = RequestAddToIndex(
            context=search_obj['context'],
            search=search_obj)
        self._message = self.MESSAGE
        self.adding = False

    def action(self):
        self.tui_frame.send_with_context(self.request_add, self.ctx_src_id)
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

    VIEW_MESSAGES = 0
    VIEW_THREADS  = 1

    def __init__(self, tui_frame, ctx_src_id, terms, view=None):
        self.tui_frame = tui_frame
        self.ctx_src_id = ctx_src_id
        self.terms = terms
        self.view = view

        if self.view is None:
            # FIXME: This is lame! Mailboxes should also have nice things
            mailbox = self.terms.startswith('mailbox:')
            self.view = self.VIEW_MESSAGES if mailbox else self.VIEW_THREADS

        self.global_hks = {
            'J': [lambda *a: None, ('top_hk', 'J:'), 'Read Next '],
            'K': [lambda *a: None, ('top_hk', 'K:'), 'Previous  ']}

        self.column_hks = [
            #('top_hk', 'A:'), 'Add To Index']
            [('top_hk', 'V:'), 'Change View']]

        self.loading = 0
        self.want_more = True
        self.want_emails = 0
        self.total_available = None
        self.search_obj = self.make_search_obj()
        self.count_obj = RequestCommand('count', args=[self.terms])

        self.walker = EmailListWalker(self)
        self.emails = self.walker.emails

        self.listbox = urwid.ListBox(self.walker)
        self.suggestions = SuggestionBox(self.tui_frame,
            update_parent=self.update_content)
        self.widgets = []

        me = 'emaillist'
        _h = self.tui_frame.conn_manager.add_handler
        self.cm_handler_ids = [
            _h(me, ctx_src_id, 'cli:search', self.incoming_result),
            _h(me, ctx_src_id, 'cli:count', self.incoming_count)]

        urwid.Pile.__init__(self, [])
        self.set_crumb()
        self.load_more()
        self.update_content()

    def cleanup(self):
        self.tui_frame.conn_manager.del_handler(*self.cm_handler_ids)
        self.search_obj = None
        self.count_obj = None
        del self.tui_frame
        del self.walker.emails
        del self.emails
        del self.listbox
        del self.widgets
        del self.suggestions

    def make_search_obj(self):
        search_args = [
            '--q=%s' % self.terms,
            '--limit=%s' % self.want_emails]

        if self.view == self.VIEW_THREADS:
            search_args.append('--output=threads_metadata')
        else:
            search_args.append('--output=metadata')

        return RequestCommand('search', args=search_args)

    def set_crumb(self, update=False):
        self.crumb = self.search_obj.get('mailbox', None)
        if not self.crumb:
            terms = self.terms
            if terms.startswith('in:'):
                terms = terms[3].upper() + terms[4:]
            elif terms == 'all:mail':
                terms = 'All Mail'
            self.crumb = terms
            if self.total_available is not None:
                self.crumb += ' (%d results)' % self.total_available
        logging.debug('CRUMB: %s' % self.crumb)
        if update:
            self.tui_frame.update_columns()

    def update_content(self, set_focus=False):
        self.widgets[0:] = []
        rows = self.tui_frame.max_child_rows()

        if not self.emails:
            message = 'Loading ...' if self.loading else 'No mail here!'
            cat = urwid.BoxAdapter(SplashCat(self.suggestions, message), rows)
            self.contents = [(cat, ('pack', None))]
            if set_focus:
                self.set_focus(0)
            return
        elif self.search_obj['req_type'] != 'search':
            pass
            #self.suggestions.set_suggestions([
            #    SuggestAddToIndex(self, self.search_obj, self.ctx_src_id)])

        # Inject suggestions above the list of messages, if any are
        # present. This can change dynamically as the backend sends us
        # hints.
        if self.walker.selected and 'mailbox' in self.search_obj:
            self.widgets.append(urwid.Columns([
                ('weight', 1, urwid.Text(('subtle',
                    'NOTE: You are operating directly on a mailbox!\n'
                    '      Tagging will add emails to the search index.\n'
                    '      Deletion cannot be undone.'))),
                ('fixed', 3, CloseButton(None))]))
        elif len(self.suggestions):
            self.widgets.append(self.suggestions)

        rows -= sum(w.rows((60,)) for w in self.widgets)
        if False and self.widgets:
            self.widgets.append(urwid.Divider())
            rows -= 1
        self.widgets.append(urwid.BoxAdapter(self.listbox, rows))

        self.contents = [(w, ('pack', None)) for w in self.widgets]
        if set_focus:
            self.set_focus(len(self.widgets)-1)

    def show_email(self, metadata):
        self.walker.expand(metadata)
        self.tui_frame.col_show(self,
            EmailDisplay(self.tui_frame, self.ctx_src_id, metadata,
                username=self.search_obj.get('username'),
                password=self.search_obj.get('password')))
        try:
            self.tui_frame.columns.set_focus_path([1])
        except IndexError:
            pass

    def load_more(self):
        now = time.time()
        if (self.loading > now - 5) or not self.want_more:
            return
        self.loading = time.time()

        self.want_emails += (self.tui_frame.max_child_rows() * 2)
        if self.search_obj['req_type'] == 'cli:search':
            self.search_obj['args'] = [
                    a for a in self.search_obj.get('args', [])
                    if not a.startswith('--limit=')
                ] + [
                    '--limit=%d' % self.want_emails]
            if self.total_available is None:
                self.tui_frame.send_with_context(
                    self.count_obj, self.ctx_src_id)
        elif self.search_obj['req_type'] == 'mailbox':
            self.search_obj['limit'] = None
        else:
            self.search_obj['limit'] = self.want_emails

        self.tui_frame.send_with_context(self.search_obj, self.ctx_src_id)

    def incoming_count(self, source, message):
        if message['req_id'] == self.count_obj['req_id']:
            for val in message['data'][0].values():
                self.total_available = val
            self.set_crumb(update=True)

    def incoming_result(self, source, message):
        try:
            first = (0 == len(self.emails))
            self.walker.set_emails(message['data'])

            #self.suggestions.incoming_message(message)

            if len(self.emails) < self.want_emails:
                self.want_more = False

            # This gets echoed back to us, if the request was retried
            # due to access controls. We may need to pass this back again
            # in order to read mail.
            self.search_obj['username'] = message.get('username')
            self.search_obj['password'] = message.get('password')
            self.loading = 0
        except:
            logging.exception('Failed to process message')
        self.update_content(set_focus=first)
