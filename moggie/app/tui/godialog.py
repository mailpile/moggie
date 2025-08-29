import copy
import logging
import urwid

from ...util.friendly import friendly_caps

from .contextlist import ContextList
from .messagedialog import MessageDialog
from .widgets import SimpleButton, Selectable


USER_TAG_CACHE = {}


class GoDialog(MessageDialog):
    """
    This dialog implements Mailpile's two-key navigation hotkeys. First
    `g` pops this dialog, and then other hotkeys can be chosen.

    Since we are popping a dialog, we will also give people a list of
    things to choose from, so they can gradually learn what works.
    """
    DEFAULT_OK = 'Cancel'

    # This is the default mapping of hot-keys to operations, along with
    # the section and text shown in the dialog window itself. The first
    # item of the tuple must match one of the operations in the op-map,
    # or be at set of search terms.
    DEFAULT_HOTKEYS = {
        'i': ('i', 'tags', 'INBOX'),
        'a': ('a', 'tags', 'All Mail'),
        'd': ('d', 'tags', 'Drafts'),
        'o': ('o', 'tags', 'OUTBOX'),
        's': ('s', 'tags', 'Sent'),
        'j': ('j', 'tags', 'Junk'),
        't': ('t', 'tags', 'Trash'),
        'b': ('b', 'tools', 'Browse for mail'),
        'c': ('c', 'tools', 'Composer'),
        'p': ('h', 'tools', 'Preferences')}

    ALPHABET = '1234567890abcdefghijklmnopqrstuvwxyz'

    HIDDEN_TAGS = ['read', 'hidden', 'urgent']

    def __init__(self, tui):
        self.tui = tui
        self.mog_ctx = mog_ctx = tui.active_mog_ctx()

        self.list_height = 0

        # FIXME: Make configurable
        self.hot_keys = copy.copy(self.DEFAULT_HOTKEYS)
        self.op_groups = {
            'tags': 'Tags',
            'contexts': 'Contexts',
            'more': 'My Tags',
            'tools': 'Tools'}

        self.op_map = {
            'i': (tui.show_search_result, mog_ctx, 'in:inbox', False),
            'a': (tui.show_search_result, mog_ctx, 'all:mail', False),
            'd': (tui.show_search_result, mog_ctx, 'in:drafts', False),
            'o': (tui.show_search_result, mog_ctx, 'in:outbox', False),
            's': (tui.show_search_result, mog_ctx, 'in:sent', False),
            'j': (tui.show_search_result, mog_ctx, 'in:junk', False),
            't': (tui.show_search_result, mog_ctx, 'in:trash', False),
            'b': (tui.show_browser, mog_ctx, True, False),
            'c': (tui.show_composer, mog_ctx),
            'p': (tui.show_preferences, mog_ctx)}

        utc = self.get_user_tag_cache(mog_ctx)
        for op in 'iadosjt':
            tag = self.op_map[op][2].split(':', 1)[1].lower()
            utc[tag] = False

        for i, (ctx_id, ctx) in enumerate(tui.context_list.contexts.items()):
            hk = '%d' % (i+1)
            self.hot_keys[hk] = (hk, 'contexts', ctx.name)  
            self.op_map[hk] = (tui.set_context, i)

        self.extra_keys = [k for k in self.ALPHABET if k not in self.hot_keys]

        self.update_tag_list(mog_ctx, [], False)
        mog_ctx.search('all:mail', output='tags',
            on_success=self.update_tag_list)

        super().__init__(tui, title='Go to ...')

    def get_user_tag_cache(self, mog_ctx):
        global USER_TAG_CACHE
        if mog_ctx.key not in USER_TAG_CACHE:
            USER_TAG_CACHE.clear()
            USER_TAG_CACHE[mog_ctx.key] = {}
        return USER_TAG_CACHE[mog_ctx.key]

    def update_tag_list(self, mog_ctx, search_result, update=True):
        utc = self.get_user_tag_cache(mog_ctx)

        for tag in search_result:
            tag = str(tag, 'utf-8').split(':', 1)[1].lower()
            if (tag not in utc
                    and tag not in self.HIDDEN_TAGS
                    and '_' not in (tag[:1], tag[-1:])):
                utc[tag] = tag

        tags = sorted([t for t in utc if utc[t]])
        for i, tag in enumerate(tags):
            self.op_map[tag] = (
                self.tui.show_search_result, mog_ctx, 'in:' + tag, False)
            key = self.extra_keys[i] if i < len(self.extra_keys) else '_'+tag
            self.hot_keys[key] = (tag, 'more', friendly_caps(tag))

        if update:
            self.update_pile(message=self.title, widgets=True)

    def make_widgets(self):
        height = 0
        columns = []
        for group, desc in self.op_groups.items():
            column = []
            column.append(urwid.Text([('go_group', desc)]))
            for key, (op, grp, op_desc) in self.hot_keys.items():
                if grp == group:
                    def _mk_cb(key):
                        return lambda *a: self.keypress(None, key)
                    if len(key) == 1:
                        prefix =  '%s: ' % key
                    else:
                        prefix = '   '
                    column.append(Selectable(urwid.Text(['  ',
                            ('go_hotkey', prefix),
                            ('go_desc', op_desc)]),
                        on_select={
                            'enter': _mk_cb(key)}))

            if len(column) > 1:
                if len(columns) < 2:
                    height = max(height, len(column))
                    columns.append(column)
                else:
                    i = 1 if len(columns[0]) > len(columns[1]) else 0
                    columns[i].append(urwid.Divider())
                    columns[i].extend(column)
                    height = max(height, len(columns[i]))

        self.list_height = height
        return [urwid.Columns(
            [('weight', 1, urwid.Pile(c)) for c in columns],
            dividechars=1)]

    def wanted_height(self):
        return super().wanted_height() + self.list_height - 1

    def keypress(self, size, key):
        action_args = self.op_map.get(self.hot_keys.get(key, [key])[0])
        if action_args:
            self._emit('close')
            try:
                action_args[0](*action_args[1:])
            except:
                logging.exception('Hot-key %s failed: %s' % (key, action_args))
            return None

        if size is not None:
            return super().keypress(size, key)
