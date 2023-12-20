import copy
import logging
import os
import urwid

from ...util.dumbcode import to_json
from ...util.friendly import *
from ..suggestions import Suggestion

from .decorations import EMOJI
from .chooseaccountdialog import ChooseAccountDialog
from .choosetagdialog import ChooseTagDialog
from .browsepathdialog import BrowsePathDialog
from .messagedialog import MessageDialog
from .suggestionbox import SuggestionBox
from .widgets import *


class SuggestAddToMoggie(Suggestion):
    MESSAGE = """\
Press ENTER to open/close directories or mailboxes. \
Click here to show configuration tools."""

    def __init__(self, parent, context):
        Suggestion.__init__(self, context, None)
        self.parent = parent

    def action(self):
        self.parent.suggestions = []
        self.parent.update_content(set_focus=True)

    def message(self):
        return self.MESSAGE


class BrowserLegend(urwid.Pile):
    BLANK = '    '
    LEGEND = [
        ['a', ('subtle', 'ccount   ('), '-', ('subtle', ': ignores parents)')],
        ['c', ('subtle', 'opy, '), 'm', ('subtle', 'ove mail')],
        ['w', ('subtle', 'atch, '), 's', ('subtle', 'ynchronize state')],
        ['t', ('subtle', 'ag: '),
            'I', ('subtle', 'nbox, '),  # Actually, INCOMING ?
            'S', ('subtle', 'ent, '),
            'J', ('subtle', 'unk, '),
            'T', ('subtle', 'rash')]]

    def __init__(self, parent):
        self.parent = parent
        self.tui = parent.tui
        self.widgets = self.make_widgets()
        urwid.Pile.__init__(self, self.widgets)

    def make_widgets(self):
        if self.parent.modified:
            widgets = [
                urwid.Text([
                    ('selcount', ' Policy changed, press W to save. ')],
                    align='center')]
        else:
            widgets = [
                urwid.Text([
                    ('subtle', 'Import policies:')])]

        for i, text in enumerate(self.LEGEND):
            wrap = 'space'
            if isinstance(text, str):
                text = ('subtle', text)
                wrap = 'clip'
            status = [(' ' * i), '.' * (len(self.BLANK) - i)]
            if text:
                widgets.append(
                    urwid.Columns([
                        ('fixed', len(self.BLANK)+1, urwid.Text(status)),
                        ('weight', 1, urwid.Text(text, wrap=wrap))]))

        widgets.append(urwid.Divider())
        return widgets

    def update(self):
        self.widgets[:] = self.make_widgets()

    def _modified_paths(self):
        for path in self.parent.modified:
            info = self.parent.walker.paths[path][BrowserListWalker.INFO]
            yield (path, info)

    def _reset_ui(self):
        self.parent.modified = set()
        self.parent.walker._modified()
        self.update()
        self.parent.update_content()

    def _on_reset(self, c):
        for path, path_info in self._modified_paths():
            path_info['policy'] = copy.deepcopy(path_info['policy.org'])
        self._reset_ui()

    def _on_save(self, c):
        updates = []
        for path, path_info in sorted(list(self._modified_paths())):
            update = copy.copy(path_info['policy'])
            update['path'] = path
            updates.append(update)
        if updates:
            # FIXME: Use moggie.set_input() ?
            self.parent.mog_ctx.import_(
                batch=True,
                config_only=True,
                stdin=to_json(updates),
                on_success=lambda m,r: True)  # Force websocket
            self._reset_ui()


class BrowserListWalker(urwid.ListWalker):
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

    def add_paths(self, paths, force_src=None):
        _so = self.src_ord
        self.paths.update(dict((p['path'], [
                # Sort by src/path
                _so.get(force_src or p['src'], 99), p['path'],
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
        for pos, p in enumerate(visible):
            src, name = p[self.SRC], p[self.PATH]
            p[self.FIRST] = (src != psrc)
            if (src == psrc) and name.startswith(ppath + '/'):
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

    def describe_cfg(self, path):
        pol = self.paths[path][self.INFO].get('policy', {})
        acct = pol.get('account')
        cfg = [
            {'-': '-', '': ' ', None: ' '}.get(acct, 'a'),
            {'copy':'c', 'move':'m', '-':'-'}.get(pol.get('copy_policy'), ' '),
            {'watch':'w', 'sync':'s', '-':'-'}.get(pol.get('watch_policy'), ' '),
            ' ']
        cfg[3] = {
            'inbox': 'I',
            'sent': 'S',
            'junk': 'J',
            'trash': 'T',
            '-': '-',
            None: ' '}.get(pol.get('tags'), 't')
        if ' ' in cfg:
            parent = '/'.join(path.split('/')[:-1])
            if parent and parent in self.paths:
                pcfg = self.describe_cfg(parent)
                for i, c in enumerate(cfg):
                    if c == ' ':
                        v = pcfg[i][1]
                        cfg[i] = ('browse_cfg_i', ' ' if (v == '-') else v)
        for i, c in enumerate(cfg):
            if isinstance(c, str):
                cfg[i] = ('browse_cfg', c)
        return cfg

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

            c = urwid.Text(self.describe_cfg(self.visible[pos][self.PATH]))
            n = urwid.Text(('browse_name', '  %s%s%s' % (indent, icon, name)),
                           wrap='ellipsis')
            i = urwid.Text(('browse_info', more), align='right', wrap='clip')
            cols = urwid.Columns([
                    ('fixed', len(BrowserLegend.BLANK), c),
                    ('weight', 15, n),
                    ('fixed', 4, i),
                ], dividechars=1)
            sel = Selectable(cols, on_select={
                'enter': _cb(self.on_browse, pos, info),
                'w': _cb(self.toggle, pos, info, 'watch_policy', 'watch'),
                's': _cb(self._fixme, pos, info, 'watch_policy', 'sync'),
                'c': _cb(self._fixme, pos, info, 'copy_policy', 'copy'),
                'm': _cb(self._fixme, pos, info, 'copy_policy', 'move'),
                'a': _cb(self.set_account, pos, info),
                'I': _cb(self.toggle, pos, info, 'tags', 'inbox'),
                'S': _cb(self.toggle, pos, info, 'tags', 'sent'),
                'J': _cb(self.toggle, pos, info, 'tags', 'junk'),
                'T': _cb(self.toggle, pos, info, 'tags', 'trash'),
                't': _cb(self.set_tag, pos, info)})
            if first:
                prefix = '\n' if (pos > 0) else ''
                return urwid.Pile([
                    urwid.Text([
                        prefix,
                        ('browse_cfg', BrowserLegend.BLANK),
                        ('browse_label', self.src_desc[src])]),
                    sel])
            else:
                return sel
        except IndexError:
            raise
        except:
            logging.exception('Failed to load message')
        raise IndexError

    def _fixme(self, *args):
        self.parent.tui.show_modal(MessageDialog,
            'Sorry, this import policy does not work yet!',
            title='Under Construction')

    def toggle(self, pos, path_info, field, value):
        if 'policy.org' not in path_info:
            path_info['policy.org'] = copy.deepcopy(path_info['policy'])

        if field == 'watch_policy' and value == 'sync':
            if not path_info['path'].startswith('imap:'):
                return

        policy = path_info['policy']
        if policy.get(field) == value:
            policy[field] = '-'
        elif policy.get(field) == '-':
            policy[field] = None
        else:
            policy[field] = value

        self.parent.modified.add(path_info['path'])
        self.parent.update_content()
        self._modified()

    def set_account(self, pos, path_info):
        if 'policy.org' not in path_info:
            path_info['policy.org'] = copy.deepcopy(path_info['policy'])
        def chose_account(context, account, pressed=None):
            path_info['policy']['account'] = account
            self.parent.modified.add(path_info['path'])
            self.parent.update_content()
            self._modified()
        self.parent.tui.show_modal(ChooseAccountDialog,
            self.parent.mog_ctx,
            title='Account for %s' % self.visible[pos][self.PATH],
            action=chose_account,
            default=path_info['policy'].get('account'),
            create=True,
            allow_none=True)

    def set_tag(self, pos, path_info):
        if 'policy.org' not in path_info:
            path_info['policy.org'] = copy.deepcopy(path_info['policy'])
        def chose_tag(context, tag, pressed=None):
            path_info['policy']['tags'] = tag
            self.parent.modified.add(path_info['path'])
            self.parent.update_content()
            self._modified()
        self.parent.tui.show_modal(ChooseTagDialog,
            self.parent.mog_ctx,
            title='Tag(s) for %s' % friendly_path(self.visible[pos][self.PATH]),
            action=chose_tag,
            default=path_info['policy'].get('tags'),
            create=True,
            multi=True,
            allow_none=True)

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
            self.parent.tui.show_mailbox(
                self.parent.mog_ctx, path_info['path'],
                keep=self.parent)

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


class Browser(urwid.Pile):
    COLUMN_NEEDS = 30
    COLUMN_WANTS = 50
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, mog_ctx, tui, browse_path):
        self.mog_ctx = mog_ctx
        self.tui = tui
        self.browse_path = browse_path
        self.crumb = browse_path if isinstance(browse_path, str) else 'Browse'

        self.modified = set([])
        self.walker = BrowserListWalker(self)
        self.legend = None
        self.listbox = urwid.ListBox(self.walker)
        self.suggestions = SuggestionBox(self.tui,
            suggestions=[SuggestAddToMoggie(self, None)],
            update_parent=self.update_content,
            omit_actions=[Suggestion.UI_BROWSE])
        self.widgets = []
        self.paths = self.walker.paths
        self.loading = True

        urwid.Pile.__init__(self, [])
        self.update_content()

        self.browse()

    def column_hks(self):
        hks = []
        if self.modified:
            hks.extend([
                ' ', ('col_hk', 'R:'), 'Reset',
                ' ', ('col_hk', 'W:'), 'Save'])
        return hks + [
            ' ', ('col_hk', 'N:'), 'New mail?',
            ' ', ('col_hk', 'O:'), 'Open']

    def keypress(self, size, key):
        if self.modified and self.legend:
            if key == 'R':
                self.legend._on_reset(key)
                return None
            if key == 'W':
                self.legend._on_save(key)
                return None

        if key == 'N':
            def popup(*a, **kw):
                self.tui.show_modal(MessageDialog,
                    'Checking any watched mailboxes for new mail.\n' +
                    'Inboxes will be filtered for junk and custom\n' +
                    'rules applied.',
                    title='Checking for new mail...')
            self.mog_ctx.new(on_success=popup)
            return None

        if key == 'O':
            self.tui.show_modal(BrowsePathDialog, self.mog_ctx)
            return None

        return super().keypress(size, key)

    def update_content(self, set_focus=False):
        rows = self.tui.max_child_rows()

        if not self.paths:
            message = 'Loading ...' if self.loading else 'Nothing here!'
            cat = urwid.BoxAdapter(SplashCat(self.suggestions, message), rows)
            self.contents = [(cat, ('pack', None))]
            return

        self.legend = None
        self.widgets = []
        if len(self.suggestions) and not self.modified:
            self.widgets.append(self.suggestions)
        else:
            self.legend = BrowserLegend(self)
            self.widgets.append(self.legend)

        rows -= sum(w.rows((30,)) for w in self.widgets)
        self.widgets.append(urwid.BoxAdapter(self.listbox, rows))

        self.contents[:] = [(w, ('pack', None)) for w in self.widgets]
        if set_focus:
            self.set_focus(len(self.contents) - 1)

    def browse(self, path_info=None):
        args = []
        callback = self.incoming_message

        if path_info:
            callback = lambda m, r: self.incoming_message(m, r, path_info)
            args.append(path_info['path'])
        elif isinstance(self.browse_path, str):
            args.append(self.browse_path)

        self.mog_ctx.browse(*args, on_success=callback)

    def incoming_message(self, mog_ctx, message, path_info=None):
        if isinstance(message, list):
            message = message[0]
        result = try_get(message, 'data', message)

        src = path_info['src'] if path_info else None
        first = len(self.walker.paths) == 0
        if isinstance(result, dict):
            try:
                for section, paths in result.items():
                    self.walker.add_paths(paths, force_src=src)
            except:
                logging.exception('Add paths failed')

        self.loading = 0
        self.update_content(set_focus=first)
