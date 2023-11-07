# TODO:
#   - Bring back "Add to the index" when viewing a mailbox (m2)
#   - Add the ability to export selected messages to a file (m2)
#   - Allow the user to toggle between message and thread views
#   - Add "search refiners" shortcuts to further narrow a search
#   - Make tagging/untagging work!
#   - Experiment with a twitter-like people-centric view?
#
import copy
import datetime
import logging
import re
import sys
import time
import urwid

from ...email.metadata import Metadata
from ..suggestions import Suggestion

from .decorations import EMOJI
from .emaildisplay import EmailDisplay
from .messagedialog import MessageDialog
from .suggestionbox import SuggestionBox
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

    def set_focus(self, focus):
        self.focus = focus
        if focus > len(self.visible) - 50:
            self.parent.load_more()
        self.parent.scounter.walker_updated()

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

    def expand(self, msg):
        self.expanded.add(msg['thread_id'])
        self.set_emails(self.emails)

    def selected_ids(self):
        _ids = set()
        if self.visible:
            _ids.add(self.visible[self.focus]['idx'])
        if self.selected:
            _ids |= set(i['idx']
                for i in self.emails if i['uuid'] in self.selected)
        return _ids

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
            if msg.get('is_hit', True):
                if self.parent.is_mailbox:
                    tf['_rank'] = -max(tf.get('_rank') or 10000000, msg['ptrs'][0][-1])
                else:
                    tf['_rank'] = max(tf.get('_rank') or 0, msg['ts'])
            depth = _depth(msg)
            if depth > 8:
                prefix = '  %d> ' % depth
                prefix += ' ' * (9 - len(prefix))
            else:
                prefix = ' ' * depth
            msg['_prefix'] = prefix

        def _sort_key(msg):
            return (-_thread_first(msg)['_rank'], msg['ts'], msg['idx'])

        self.visible.sort(key=_sort_key)

        # Keep the focus in the right place!
        if focus_uuid is not None:
            for i, e in enumerate(self.visible):
                if e['uuid'] == focus_uuid:
                    self.focus = i

        self._modified()

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
            dt = md.get('ts') or md.get('_rank')
            dt = datetime.datetime.fromtimestamp(dt) if dt else 0
            if self.selected_all or focused or uuid in self.selected:
                prefix = 'check'
                attrs = '>    <'
                dt = dt.strftime('%Y-%m  ' + EMOJI.get('selected', 'X')) if dt else ''
            else:
                attrs = '(    )'
                prefix = 'list' if md.get('is_hit', True) else 'more'
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
                'enter': lambda x: self.parent.show_email(
                    self.visible[pos], selected=(uuid in self.selected)),
                'x': lambda x: self.check(uuid),
                ' ': lambda x: self.check(uuid, display=True)})
        except IndexError:
            logging.exception('Failed to load message')
            pass
        except:
            logging.exception('Failed to load message')
        raise IndexError

    def check(self, uuid, display=False):
        # FIXME: The spacebar still doesn't quite work elegantly.
        if self.selected_all:
            self.selected_all = False
            for visible in self.visible:
                self.selected.add(visible['uuid'])
            had_any = False  # Force a redraw
        else:
            had_any = (len(self.selected) > 0)

        if uuid in self.selected:
            if not display:
                # FIXME: If message is currently displayed, update!
                self.selected.remove(uuid)
            down = True
        else:
            self.selected.add(uuid)
            down = not display
        have_any = (len(self.selected) > 0)

        # Warn the container that our selection state has changed.
        if had_any != have_any:
            self.parent.update_content()

        # FIXME: There must be a better way to do this?
        self._modified()
        if down:
            self.parent.keypress((100,), 'down')
        if display:
            self.parent.keypress((100,), 'enter')


class SuggestAddToIndex(Suggestion):
    MESSAGE = 'Add these messages to the search index'

    def __init__(self, parent, search_obj):
        Suggestion.__init__(self, parent.mog_ctx.key, None)
        self.parent = parent
        self.tui = parent.tui
        self.adding = False
        self._message = self.MESSAGE

    def action(self):
        logging.error('FIXME: Add to index')
        self.adding = True

    def message(self):
        # FIXME: If updates are happening, turn into a progress
        #        reporting message?
        if self.adding:
            return 'ADDING, WOOO'
        return self._message


class SelectionCounter(urwid.Text):
    def __init__(self, walker):
        self.walker = walker
        self.fmt = '\nSelected %d messages'
        super().__init__(
            [('subtle', self.fmt % len(self.walker.selected_ids()))],
            align='center')

    def walker_updated(self):
        self.set_text(
            [('subtle', self.fmt % len(self.walker.selected_ids()))])


class EmailList(urwid.Pile):
    COLUMN_NEEDS = 50
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    VIEW_MESSAGES = 0
    VIEW_THREADS  = 1
    VIEWS = {
        VIEW_MESSAGES: 'metadata',
        VIEW_THREADS: 'threads_metadata'}

    TAG_OP_MAP = {
        't': ('TAG',   'Tag selected messages', 'Tagged'),
        'E': ('UNTAG', 'Remove tags: %s',       'Untagged'),
        'I': ('+read', 'Mark messages read',    'Marked read'),
        '!': ('+junk', 'Move to junk',          'Moved to junk'),
        '#': ('+trash', 'Move to trash',        'Moved to trash')}

    def __init__(self, mog_ctx, tui, terms, view=None):
        self.name = 'emaillist-%.5f' % time.time()
        self.mog_ctx = mog_ctx
        self.tui = tui
        self.terms = terms
        self.view = view

        self.is_mailbox = False
        if self.view is None:
            if self.terms.startswith('mailbox:'):
                self.is_mailbox = self.terms[8:]
            self.view = self.VIEW_THREADS

        self.global_hks = {
            ' ': True,
            'J': [lambda *a: None, ('top_hk', 'J:'), 'Read Next '],
            'K': [lambda *a: None, ('top_hk', 'K:'), 'Previous  ']}

        self.loading = 0
        self.want_more = True
        self.want_emails = 0
        self.total_available = None
        self.webui_state = {}

        self.walker = EmailListWalker(self)
        self.emails = self.walker.emails
        self.scounter = SelectionCounter(self.walker)

        self.listbox = urwid.ListBox(self.walker)
        self.suggestions = SuggestionBox(tui, update_parent=self.update_content)
        self.widgets = []

        urwid.Pile.__init__(self, [])
        self.set_crumb()
        self.load_more()
        self.update_content()

    def cleanup(self):
        self.mog_ctx.moggie.unsubscribe(self.name)

    def keypress(self, size, key):
        # FIXME: Should probably be using CommandMap !
        if key in (' ',):
            # FIXME: Is there a better way?
            size = self.tui.screen.get_cols_rows()
            return self.listbox.keypress(size, key)
        if key == 'J':
            # FIXME: Is there a better way?
            size = self.tui.screen.get_cols_rows()
            self.listbox.keypress(size, 'down')
            self.listbox.keypress(size, 'enter')
            return None
        if key == 'K':
            # FIXME: Is there a better way?
            size = self.tui.screen.get_cols_rows()
            self.listbox.keypress(size, 'up')
            self.listbox.keypress(size, 'enter')
            return None
        if key == 'D':
            self.on_export()
            return None
        if key == 'V':
            self.on_toggle_view()
            return None
        if key == 'A':
            self.on_add_to_index()
            return None
        if key == 'X':
            self.on_select_all()
            return None
        if key in self.get_tag_op_map():
            self.on_tag_op(key)
            return None
        return super().keypress(size, key)

    def search(self, limit=False):
        self.mog_ctx.search(
            q=self.terms,
            output=self.VIEWS.get(self.view, 'metadata'),
            limit=self.want_emails if (limit is False) else (limit or '-'),
            json_ui_state=True,
            on_success=self.incoming_result)

    def set_crumb(self, update=False):
        self.crumb = self.is_mailbox
        if not self.crumb:
            terms = self.terms
            if terms.startswith('in:'):
                terms = terms[3].upper() + terms[4:]
            elif terms == 'all:mail':
                terms = 'All Mail'
            self.crumb = terms
            if self.total_available is not None:
                self.crumb += ' (%d results)' % self.total_available
        if update:
            self.tui.update_columns()

    def update_content(self, set_focus=False):
        self.widgets[0:] = []
        rows = self.tui.max_child_rows()

        if not self.emails:
            message = 'Loading ...' if self.loading else 'No mail here!'
            cat = urwid.BoxAdapter(SplashCat(self.suggestions, message), rows)
            self.contents = [(cat, ('pack', None))]
            if set_focus:
                self.set_focus(0)
            return

        # Inject suggestions above the list of messages, if any are
        # present. This can change dynamically as the backend sends us
        # hints.
        if self.walker.selected_all or self.walker.selected:
            if self.walker.selected_all:
                count = urwid.Text(
                    [('subtle', '\nAll matching messages are selected.')],
                    align='center')
            else:
                count = self.scounter
            ops = []
            for hotkey, (tagop, desc, _) in self.get_tag_op_map().items():
                ops.extend([
                    ('white', '%s:' % hotkey), ('subtle', desc),
                    ('subtle', '\n')])
            ops.pop(-1)
            self.widgets.append(urwid.Columns([
                ('weight', 1, count),
                ('weight', 1, urwid.Text(ops, align='left'))]))
            divide = True
        elif len(self.suggestions):
            self.widgets.append(self.suggestions)
            divide = False

        rows -= sum(w.rows((60,)) for w in self.widgets)
        if False and divide:  # FIXME
            self.widgets.append(urwid.Divider())
            rows -= 1
        self.widgets.append(urwid.BoxAdapter(self.listbox, rows))

        self.contents = [(w, ('pack', None)) for w in self.widgets]
        if set_focus:
            self.set_focus(len(self.widgets)-1)

    def show_email(self, metadata, selected=False):
        self.walker.expand(metadata)
        self.tui.col_show(self,
            EmailDisplay(self.mog_ctx, self.tui, metadata,
                selected=selected))
                # FIXME:
                #username=self.search_obj.get('username'),
                #password=self.search_obj.get('password'),

    def load_more(self):
        now = time.time()
        if (self.loading > now - 5) or not self.want_more:
            return
        self.loading = time.time()

        self.want_emails += max(100, self.tui.max_child_rows() * 2)

        if self.is_mailbox:
            self.search(limit=None)
        else:
            self.search(limit=self.want_emails)
            if self.total_available is None:
                self.mog_ctx.count(self.terms, on_success=self.incoming_count)

    def incoming_count(self, mog_ctx, message):
        data = try_get(message, 'data', message)
        if data:
            for val in data[0].values():
                self.total_available = val
            self.set_crumb(update=True)

    def incoming_result(self, mog_ctx, message):
        data = try_get(message, 'data', message)
        try:
            first = (0 == len(self.emails))
            self.webui_state = data.pop(0)
            self.walker.set_emails(data)

            # FIXME: Should the back-end make this easier for us?
            terms = (self.webui_state['details'].get('terms', '')
                .replace('+', '').replace('-', '')).split()
            self.webui_state['query_tags'] = [
                word for word in terms if word.startswith('in:')]

            #self.suggestions.incoming_message(message)

            if len(self.emails) < self.want_emails:
                self.want_more = False

            # FIXME: This is now broken!
            # This gets echoed back to us, if the request was retried
            # due to access controls. We may need to pass this back again
            # in order to read mail.
            #self.search_obj['username'] = message.get('username')
            #self.search_obj['password'] = message.get('password')
            #self.loading = 0
        except:
            logging.exception('Failed to process message')
        self.update_content(set_focus=first)

    def column_hks(self):
        hks = []
        if self.emails:
            hks.extend([' ', ('col_hk', 'x:'), 'Select?'])
            hks.extend([' ', ('col_hk', 'D:'), 'Download'])
        if self.is_mailbox:
            hks.extend([' ', ('col_hk', 'A:'), 'Add to moggie'])
        else:
            # FIXME: Mailboxes should have multiple views too
            pass  # hks.extend([' ', ('col_hk', 'V:'), 'Change View'])

        # FIXME: Saving searches!

        return hks

    def get_tag_op_map(self):
        # FIXME: Adapt to mailbox searches?
        opmap = copy.copy(self.TAG_OP_MAP)
        tags = self.webui_state.get('query_tags')
        if tags:
            untag = ' '.join('-%s' % t for t in tags)
            tdesc = ', '.join(t[3:] for t in tags)
            opmap['E'] = (untag, opmap['E'][1] % tdesc, opmap['E'][2])
        else:
            del opmap['E']
        # FIXME: If no tags in the search result, omit E
        #        If there are, update the description
        return opmap

    def on_tag_op(self, hotkey=None, tag_op=None, comment=None):
        if tag_op is None:
            tdc = self.get_tag_op_map().get(hotkey)
            if tdc:
                tag_op, desc, comment = tdc
        if tag_op == 'TAG':
            pass  # FIXME: Pop up tag dialog
        else:
            self.run_tag_op(tag_op, comment)

    def run_tag_op(self, tag_op, comment):
        if 'details' in self.webui_state:
            search_terms = self.webui_state['details']['terms'].split()
        else:
            logging.debug('STATE: %s' % self.webui_state)
            search_terms = []

        if not self.walker.selected_all:
            idlist = ('(%s)' % ' OR '.join('id:%s' % _id
                for _id in self.walker.selected_ids()))
            if search_terms and search_terms[0].startswith('mailbox:'):
                search_terms.append(idlist)
            else:
                search_terms = [idlist]

        args = tag_op.split()
        if comment:
            args.append('--comment=%s' % comment)
        args.append('--')
        args.extend(search_terms)
        self.mog_ctx.tag(*args,
            on_success=self.on_tagged,
            on_error=self.on_tag_failed)

        #logging.debug('FIXME: Should tag %s with %s' % (tag_op, search_terms))

    def on_tagged(self, *args, **kwargs):
        logging.debug('FIXME: on_tagged(%s, %s)' % (args, kwargs))

    def on_tag_failed(self, *args, **kwargs):
        logging.error('FIXME: on_tag_failed(%s, %s)' % (args, kwargs))

    def on_select_all(self):
        self.walker.selected = set()
        self.walker.selected_all = not self.walker.selected_all
        self.walker._modified()
        self.update_content()

    def on_toggle_view(self):
        self.tui.topbar.open_with(
            MessageDialog, 'FIXME: Toggling views does not work yet')

    def on_export(self):
        self.tui.topbar.open_with(
            MessageDialog, 'FIXME: Exporting mail does not work yet')

    def on_add_to_index(self):
        # If some messages are selected, tell user:
        #    - Adding N messages to search engine
        # If none or all are selected:
        #    - Adding all messages to search engine
        #
        # Always Offer toggles:
        #   [ ] Treat messages as "incoming" (filter for spam, add to inbox)
        #
        # If adding entire mailbox:
        #   ( ) Treat mailbox as an Inbox, check frequently for new mail
        #   ( ) Check periodically for new mail
        #   ( ) Only add these messages
        #
        self.tui.topbar.open_with(
            MessageDialog, 'FIXME: Adding to the index does not work yet')
