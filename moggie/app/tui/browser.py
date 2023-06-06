import logging
import os
import urwid

from ...api.requests import RequestCommand
from ...util.friendly import *
from ..suggestions import Suggestion

from .decorations import EMOJI
from .suggestionbox import SuggestionBox
from .widgets import *


class BrowserListWalker(urwid.ListWalker):
    def __init__(self, parent):
        from ..cli.admin import CommandBrowse
        self.focus = 0
        self.paths = {}
        self.visible = []
        self.selected = set()
        self.selected_all = False
        self.parent = parent
        self.src_ord = CommandBrowse.SRC_ORDER
        self.src_desc = CommandBrowse.SRC_DESCRIPTIONS

    def __len__(self):
        return len(self.visible)

    def set_focus(self, focus):
        self.focus = focus

    def next_position(self, pos):
        if pos + 1 < len(self.visible):
            return pos + 1
        raise IndexError

    def prev_position(self, pos):
        if pos > 0:
            return pos - 1
        raise IndexError

    def positions(self, reverse=False):
        if reverse:
            return reversed(range(0, len(self.visible)))
        return range(0, len(self.visible))

    SECTION  = 0
    PATH     = 1
    VISIBLE  = 2
    FIRST    = 3
    INDENT   = 4
    SRC      = 5
    NAME     = 6
    INFO     = 7
    LOADED   = 8
    EXPANDED = 9

    def add_paths(self, paths, force_src=None):
        _so = self.src_ord
        self.paths.update(dict((p['path'], [
                _so.get(force_src or p['src'], 99), p['path'],  # Sort by src/path
                True,     # 2 == Visible?
                False,    # 3 == First?
                '',       # 4 == Indent
                force_src or p['src'], # 5 == Source
                p['path'],# 6 == Friendly name
                p,        # 7 == Info
                False,    # 8 == Loaded?
                False     # 9 == Expanded?
            ]) for p in paths if p['path'] not in self.paths))

        visible = [p for p in self.paths.values() if p[self.VISIBLE]]
        visible.sort()

        psrc, ppath, pslashes = '-', '-', 0
        for p in visible:
            src, name = p[self.SRC], p[self.PATH]
            p[self.FIRST] = (src != psrc)
            if (src == psrc) and name.startswith(ppath):
                indent = sum(1 for c in name if c == '/') - pslashes
                name = os.path.basename(name)
            else:
                indent, psrc, ppath = 0, src, name
                pslashes = sum(1 for c in name[:-1] if c == '/')

            if src == 'mailpilev1':
                name = name.split('Mailpile/', 1)[-1]
            elif src == 'thunderbird' and name[:5] != 'imap:':
                name = name.split('.thunderbird/', 1)[-1]

            p[self.INDENT] = '  ' * max(0, indent)
            p[self.NAME] = name

        self.visible[:] = visible
        self._modified()

    def __getitem__(self, pos):
        def _cb(cb, *args):
            return lambda x: cb(*args)
        try:
            first, indent, src, name, info = (
                self.visible[pos][self.FIRST:self.INFO+1])

            magic = info.get('magic')
            icon = EMOJI.get(magic[0]) if magic else ''
            if not icon:
                icon = EMOJI.get(
                        'server' if name.startswith('imap:') else
                        'folder' if info.get('is_dir') else
                        'file')

            if 'size' in info and not info.get('is_dir'):
                more = friendly_bytes(info['size'])
            else:
                more = ''

            n = urwid.Text(('browse_name', '  %s%s%s' % (indent, icon, name)),
                           wrap='ellipsis')
            i = urwid.Text(('browse_info', more), align='right', wrap='clip')
            cols = urwid.Columns([
                    ('weight', 15, n),
                    ('fixed', 4, i),
                ], dividechars=1)
            sel = Selectable(cols, on_select={
                'enter': _cb(self.on_browse, pos, info)})
            if first:
                prefix = '\n' if (pos > 0) else ''
                return urwid.Pile([
                    urwid.Text(('browse_label', prefix + self.src_desc[src])),
                    sel])
            else:
                return sel
        except IndexError:
            raise
        except:
            logging.exception('Failed to load message')
        raise IndexError

    def on_browse(self, pos, path_info):
        entry = self.visible[pos]

        if entry[self.LOADED]:
            # This is a little bit weird, but I think
            # it is weird in a useful way?
            show = not entry[self.EXPANDED]
            for e in self.paths.values():
                if (e[self.PATH].startswith(entry[self.PATH] + '/')
                        and e[self.SRC] == entry[self.SRC]):
                    if e[self.LOADED]:
                        e[self.EXPANDED] = show
                    else:
                        e[self.VISIBLE] = show
            entry[self.EXPANDED] = show
            self.add_paths({})
        else:
            entry[self.LOADED] = entry[self.EXPANDED] = True
            self.parent.browse(path_info)

        if path_info.get('magic'):
            self.parent.tui_frame.show_mailbox(
                path_info['path'], keep=self.parent)

    def on_check(self, pos, path_info):
        return

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
        #self.parent.keypress((100,), 'down')


class SuggestAddToIndex(Suggestion):
    MESSAGE = 'Add these messages to the search index'

    def __init__(self, tui_frame, browse_obj, ctx_src_id):
        Suggestion.__init__(self, context, None)  # FIXME: Config?
        self.tui_frame = tui_frame
        self.ctx_src_id = ctx_src_id
        self.request_add = RequestAddToIndex(
            context=browse_obj['context'],
            search=browse_obj)
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
    COLUMN_NEEDS = 20
    COLUMN_WANTS = 35
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, ctx_src_id, browse_path):
        self.tui_frame = tui_frame
        self.ctx_src_id = ctx_src_id
        self.browse_path = browse_path
        self.crumb = browse_path if isinstance(browse_path, str) else 'Browse'

        self.column_hks = []  #('col_hk', 'A:'), 'Add To Index']

        self.walker = BrowserListWalker(self)
        self.listbox = urwid.ListBox(self.walker)
        self.browse_obj = self.get_browse_obj()
        self.suggestions = SuggestionBox(self.tui_frame,
            update_parent=self.update_content,
            omit_actions=[Suggestion.UI_BROWSE])
        self.widgets = []
        self.paths = self.walker.paths
        self.loading = True

        _ah = self.tui_frame.conn_manager.add_handler
        self.cm_handler_ids = [
            _ah('browser', ctx_src_id, 'cli:browse', self.incoming_message)]
        self.tui_frame.send_with_context(self.browse_obj, self.ctx_src_id)

        urwid.Pile.__init__(self, [])
        self.update_content()

    def cleanup(self):
        self.tui_frame.conn_manager.del_handler(*self.cm_handler_ids)
        self.browse_obj = None
        del self.tui_frame
        del self.walker.paths
        del self.walker.visible
        del self.walker
        del self.paths
        del self.listbox
        del self.widgets

    def get_browse_obj(self):
        if isinstance(self.browse_path, str):
            return RequestCommand('browse', args=[self.browse_path])
        else:
            return RequestCommand('browse', args=[])

    def browse(self, path_info):
        self.browse_obj['args'] = [path_info['path']]
        self.browse_obj['req_id'] = self.browse_obj['req_id'].split('=')[0]
        self.browse_obj['req_id'] += ('=' + path_info['src'])
        self.tui_frame.send_with_context(self.browse_obj, self.ctx_src_id)

    def update_content(self):
        rows = self.tui_frame.max_child_rows()

        if not self.paths:
            message = 'Loading ...' if self.loading else 'Nothing here!'
            cat = urwid.BoxAdapter(SplashCat(self.suggestions, message), rows)
            self.contents = [(cat, ('pack', None))]
            return

        self.widgets = []
        if len(self.suggestions):
            self.widgets.append(self.suggestions)

        rows -= sum(w.rows((30,)) for w in self.widgets)
        self.widgets.append(urwid.BoxAdapter(self.listbox, rows))

        self.contents[:] = [(w, ('pack', None)) for w in self.widgets]

    def incoming_message(self, source, message):
        req_id = message.get('req_id')
        if (not self.browse_obj) or (req_id != self.browse_obj['req_id']):
            return

        for result in message.get('data', []):
            if not isinstance(result, dict):
                continue
            try:
                src = None
                if '=' in req_id:
                    src = req_id.split('=')[-1]
                for section, paths in result.items():
                    self.walker.add_paths(paths, force_src=src)
            except:
                logging.exception('Add paths failed')

        self.loading = 0
        self.update_content()
