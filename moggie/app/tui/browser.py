import logging
import urwid

from ...api.requests import RequestBrowse
from ..suggestions import Suggestion

from .suggestionbox import SuggestionBox
from .widgets import *


class BrowserListWalker(urwid.ListWalker):
    def __init__(self, parent):
        self.focus = 0
        self.paths = []
        self.selected = set()
        self.selected_all = False
        self.parent = parent

    def __len__(self):
        return sum(1 for path in self.paths if path[0])

    def add_paths(self, parent, paths):
        def _mk_path(p):
            displayed = 1
            return [displayed, indent, path]
        self.paths.extend(_mk_path(path) for path in paths)
        self.paths.sort()
        self._modified()

    def set_focus(self, focus):
        self.focus = focus
        #if focus > len(self.paths) - 100:
        #    self.parent.load_more()

    def next_position(self, pos):
        if pos + 1 < len(self.paths):
            return pos + 1
        #self.parent.load_more()
        raise IndexError

    def prev_position(self, pos):
        if pos > 0:
            return pos - 1
        raise IndexError

    def positions(self, reverse=False):
        if reverse:
            return reversed(range(0, len(self.paths)))
        return range(0, len(self.paths))

    def __getitem__(self, pos):
        try:
            filename = self.paths[pos][-1]
            cols = urwid.Columns([
              ('weight', 15, urwid.Text(('browse_file', filename), wrap='clip')),
              (10,           urwid.Text(('_date', dt), align='left'))],
              dividechars=1)
            return Selectable(cols, on_select={
                'enter': lambda x: self.parent.open_path(filename),
                'x': lambda x: self.check(filename)})
        except IndexError:
            pass
        except:
            logging.exception('Failed to load message')
        raise IndexError

    def check(self, path, display=None):
        had_any = (len(self.selected) > 0)
        if path in self.selected and not display:
            self.selected.remove(path)
        else:
            self.selected.add(path)
        have_any = (len(self.selected) > 0)

        # Warn the container that our selection state has changed.
        if had_any != have_any:
            self.parent.update_content()

        self._modified()
        # FIXME: There must be a better way to do this...
        self.parent.keypress((100,), 'down')


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


class Browser(urwid.Pile):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, search_obj, ctx_src_id):
        self.ctx_src_id = ctx_src_id
        self.search_obj = search_obj
        self.tui_frame = tui_frame
        self.conn_manager = tui_frame.conn_manager

        self.crumb = search_obj.get('mailbox', None)
        if not self.crumb:
            terms = search_obj.get('terms', 'FIXME')
            if terms.startswith('in:'):
                terms = terms[3].upper() + terms[4:]
            elif terms == 'all:mail':
                terms = 'All Mail'
            self.crumb = terms

        self.global_hks = {
            'J': [lambda *a: None, ('top_hk', 'J:'), 'Read Next '],
            'K': [lambda *a: None, ('top_hk', 'K:'), 'Previous  ']}

        self.column_hks = [('top_hk', 'A:'), 'Add To Index']

        self.walker = BrowserListWalker(self)
        self.paths = self.walker.paths
        self.listbox = urwid.ListBox(self.walker)
        self.suggestions = SuggestionBox(self.tui_frame,
            update_parent=self.update_content)
        self.widgets = []

        self.cm_handler_id = self.conn_manager.add_handler(
            'browser', ctx_src_id, search_obj, self.incoming_message)

        self.loading = 0
        self.want_more = True
        self.load_more()

        urwid.Pile.__init__(self, [])
        self.update_content()

    def update_content(self):
        self.widgets[0:] = []
        rows = self.tui_frame.max_child_rows()

        if not self.paths:
            message = 'Loading ...' if self.loading else 'No mail here!'
            cat = urwid.BoxAdapter(SplashCat(self.suggestions, message), rows)
            self.contents = [(cat, ('pack', None))]
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

    def cleanup(self):
        self.conn_manager.del_handler(self.cm_handler_id)
        self.search_obj = None
        del self.tui_frame
        del self.conn_manager
        del self.walker.emails
        del self.walker
        del self.paths
        del self.listbox
        del self.widgets

    def show_email(self, metadata):
        self.tui_frame.col_show(self,
            EmailDisplay(self.tui_frame, self.ctx_src_id, metadata))
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
            'skip': len(self.paths),
            'limit': min(max(500, 2*len(self.paths)), 10000)})
        self.tui_frame.send_with_context(self.search_obj, self.ctx_src_id)

    def incoming_message(self, source, message):
        logging.debug('[%s] => %.128s' % (source, message))
        if (not self.search_obj
                or message.get('req_id') != self.search_obj['req_id']):
            return
        try:
            self.suggestions.incoming_message(message)
            self.walker.add_emails(message['skip'], message['emails'])

            self.want_more = (message['limit'] == len(message['emails']))
            self.loading = 0
            self.load_more()
        except:
            logging.exception('Failed to process message')
        self.update_content()
