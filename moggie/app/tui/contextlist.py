import logging
import time
import urwid

from moggie import MoggieContext

from .decorations import EMOJI
from .widgets import *


class ContextList(urwid.ListBox):
    COLUMN_NEEDS = 18
    COLUMN_WANTS = 18
    COLUMN_FIT = 'fixed'
    COLUMN_STYLE = 'sidebar'

    COUNT_INTERVAL = 60

    # We define these common user-interface elements here; whether
    # they are actually shown depends on the context config, but we want
    # them to show up in a stable order so the UI is predictable.
    TAG_ITEMS = [
        ('inbox',  (('i', 'INBOX',    'in:inbox'),
#                   ('c', 'Calendar', ''),
#                   ('p', 'People',   ''),
                    ('a', 'All Mail', 'all:mail'))),
        ('outbox', (('8', 'OUTBOX',   'in:outbox'),)),
        ('sent',   (('9', 'Sent',     'in:sent'),)),
        ('drafts', (('0', 'Drafts',   'in:drafts'),)),
        ('junk',   (('s', 'Junk',     'in:junk'),)),
        ('trash',  (('d', 'Trash',    'in:trash'),))]
    TAG_KEYS = 'wertyu'

    def __init__(self, tui, update_parent=None, expanded=0, first=False):
        self.name = 'contextlist-%.4f' % time.time()
        self.moggie = tui.moggie
        self.mog_ctx0 = tui.mog_ctx0
        self.moggies = {tui.moggie.name: tui.moggie}
        self.tui = tui
        self.update_parent = update_parent or (lambda: None)
        self.expanded = expanded
        self.first = first

        self.crumb = ''
        self.active = None
        self.default_action = None
        self.walker = urwid.SimpleListWalker([])
        self.v_history = []
        self.v_history_max = 4
        self.global_hks = {}

        self.order = []
        self.contexts = {}
        self.tag_counted = 0
        self.tag_counts = {}
        self.update_content()
        self.awaiting_counts = {}

        urwid.ListBox.__init__(self, self.walker)

        # FIXME: cm.add_handler(me, '*', 'pong', self.incoming_pong)
        tui.moggie.context(output='details', on_success=self.incoming_contexts)

    def cleanup(self):
        self.moggie.unsubscribe(self.name)

    def keypress(self, size, key):
        if key == 'B':
            self.tui.show_browser(self.mog_ctx0, history=False)
            return None
        elif key in self.global_hks:
            hk = self.global_hks[key]
            if isinstance(hk, list):
                hk = hk[0]
            if hk:
                hk()
                return None
        return super().keypress(size, key)

    def expand(self, i, activate=True):
        self.expanded = i
        self.update_content()
        if activate:
            self.activate_default_view()

    def column_hks(self):
        hks = []
        hks.extend([' ', ('col_hk', 'B:'), 'Browse'])
        return hks

    def activate_default_view(self):
        if self.default_action is not None:
            self.default_action(None)
            self.first = False
            return True
        self.first = True
        return False

    def add_history(self, desc, recreate, icon='-'):
        self.v_history = [vh for vh in self.v_history if (vh[1] != desc)]
        self.v_history = self.v_history[-(self.v_history_max-1):]
        self.v_history.append((icon, desc, self.expanded, recreate))
        self.update_content()

    def show_history(self, icon, desc, ctx_src_id, recreate):
        self.expand(ctx_src_id, activate=False)
        recreate()

    def show_overview(self, context):
        logging.debug('FIXME: Should show context %s' % context)
        self.tui.show_browser(self.mog_ctx0, history=False)

    def show_connections(self, context):
        logging.debug('FIXME: Should show context connections')

    def show_account(self, account):
        logging.debug('FIXME: Should show account details: %s' % account)

    def show_search(self, terms, ctx_src_id):
        mog_ctx = self.contexts.get(ctx_src_id, self.mog_ctx0)
        return self.tui.show_search_result(mog_ctx, terms, history=False)

    def request_counts(self, moggie):
        self.tag_counted = time.time()
        self.awaiting_counts = {}
        prefix = '%s/' % moggie.name
        for ctx_src_id, mog_ctx in self.contexts.items():
            if not ctx_src_id.startswith(prefix):
                continue
            args = ['--multi']
            all_tags = mog_ctx.tags + mog_ctx.ui_tags
            all_tags.extend(t for t,i in self.TAG_ITEMS if t not in all_tags)
            if all_tags:
                for tag in set(all_tags):
                    tag = tag.lower()
                    args.append('--q=in:%s' % tag)
                    args.append('--q=in:%s -tag:read' % tag)
                logging.debug('request count: %s => %s' % (args, mog_ctx))
                mog_ctx.count(*args, on_success=self.incoming_counts)

    def incoming_pong(self, source, message):
        # FIXME: We should put in some better plumbing, so the backend
        #        app notifies us proactively when counts change. And
        #        especially if the config changes, which since there can
        #        be multiple front-ends, is bound to happen!
        if self.tag_counted < time.time() - self.COUNT_INTERVAL:
            self.request_counts()

    def incoming_contexts(self, moggie, message):
        data = try_get(message, 'data', message)
        for ctx_id, info in data[0].items():
            ctx_src_id = '%s/%s' % (moggie.name, ctx_id)
            if ctx_src_id not in self.contexts:
                info['ctx_src_id'] = ctx_src_id
                self.order.append(ctx_src_id)
                self.contexts[ctx_src_id] = MoggieContext(moggie, info=info)
                self.tag_counts[ctx_src_id] = {}
            else:
                self.contexts[ctx_src_id].update(info)
        self.update_content()
        self.request_counts(moggie)
        self.update_parent()
        if self.first:
            self.activate_default_view()

    def incoming_counts(self, mog_ctx, message):
        ctx_src_id = mog_ctx.get('ctx_src_id')
        data = try_get(message, 'data', message)
        if data and ctx_src_id:
            self.tag_counts[ctx_src_id] = counts = {}
            for search, count in data[0].items():
                search = search[3:].replace(' -tag:read', '*')
                counts[search] = count
            self.update_content()

    def update_content(self):
        def _sel_ctx(which):
            # This goes through the main frame in case it wants to know
            # things have changed and coordinate with other UI elements.
            return lambda *x: self.tui.set_context(which)
        def _sel_email(account):
            return lambda *x: self.show_account(account)
        def _sel_search(terms, ctx_src_id):
            return lambda *x: self.show_search(terms, ctx_src_id)
        def _sel_mailbox(path, ctx_src_id):
            # FIXME: Wrong moggie?
            return lambda *x: self.tui.show_mailbox(
                self.moggie, path, ctx_src_id, history=False)
        def _sel_history(*args):
            return lambda *x: self.show_history(*args)

        def _friendly_count(tc):
            if tc < 1:
                return ''
            if tc < 1000:
                return ' %d' % tc
            if tc < 1000000:
                return ' %dk' % (tc // 1000)
            return 'oo'

        self.global_hks = {}
        self.crumb = '(moggie is unconfigured)'
        widgets = []
        if self.v_history:
            widgets.append(urwid.Text(('subtle', 'Recent:')))
            for entry in reversed(self.v_history):
                icon, desc = entry[:2]
                widgets.append(Selectable(
                    urwid.Text(
                        [('subtle', '%s %s' % (icon, desc))],
                        'left', 'clip'),
                    on_select={'enter': _sel_history(*entry)}))
            widgets.append(urwid.Divider())
            #widgets.append(urwid.Text([('subtle', '_'*20)], 'left', 'clip'))

        last_ctx_name = '-:-!-:-'
        self.order.sort(
            key=lambda c: (0 if (c == 'local_app/Context 0') else 1, c))
        for i, ctx_src_id in enumerate(self.order):
            mog_ctx = self.contexts[ctx_src_id]
            name = mog_ctx.name
            if name.startswith(last_ctx_name+' '):
                name = ' - ' + name[len(last_ctx_name)+1:]
            else:
                last_ctx_name = name

#           sc = ('g%d:' % (i+1)) if (i < 8) else '   '
            ctx_name = urwid.Text([
#               ('hotkey', sc),
                ('subtle', name)], 'left', 'clip')

            if self.expanded in (i, ctx_src_id):
                self.expanded = ctx_src_id
                last_ctx_name = name
                self.active = mog_ctx
                self.crumb = mog_ctx.name
                widgets.append(Selectable(urwid.AttrMap(ctx_name,
                    {None: 'active', 'subtle': 'active', 'hotkey': 'act_hk'}),
                    on_select={'enter': self.show_overview}))
                self.default_action = self.show_overview
#               widgets.append(Selectable(urwid.Text(
#                   [('subtle', 'live:1')], 'right', 'clip'),
#                   on_select={'enter': self.show_connections}))
            else:
                widgets.append(Selectable(ctx_name,
                    on_select={'enter': _sel_ctx(i)}))

            def_act = None
            if self.expanded == ctx_src_id:
                acount = 0
                for ai, (a_id, acct) in enumerate(mog_ctx.accounts.items()):
                    action = _sel_email(acct)
                    widgets.append(Selectable(urwid.Padding(
                            urwid.Text(('email', acct['name']), 'left', 'clip'),
                            left=1, right=1),
                        on_select={'enter': action}))  # FIXME
                    if ai == 0:
                        self.default_action = def_act = action
                    acount += 1

                    for mi, mailbox in enumerate(acct.get('mailboxes', [])):
                        label, _, _, path = mailbox.split(':', 3)
                        if not label:
                            continue
                        action = _sel_mailbox(path, ctx_src_id)
                        label = '%s %s' % (EMOJI.get('mailbox', '-'), label)
                        widgets.append(Selectable(urwid.Padding(
                                urwid.Text(('email', label), 'left', 'clip'),
                                left=2, right=1),
                            on_select={'enter': action}))  # FIXME
                        if mi == 0:
                            self.default_action = def_act = action

                    if acount > 3:
                        pass  # FIXME: Add a "more" link, break loop
                if acount:
                    widgets.append(urwid.Divider())

                shown = []
                all_tags = mog_ctx.tags + mog_ctx.ui_tags
                for tag, items in self.TAG_ITEMS:
                    all_lc_tags = [t.lower() for t in all_tags]
                    if tag in all_lc_tags or not all_lc_tags:
                        tc = self.tag_counts[ctx_src_id].get(tag.lower()+'*', 0)
                        for ti, (sc, name, search) in enumerate(items):
                            os = search and {
                                'enter': _sel_search(search, ctx_src_id)}
                            if sc and os:
                                self.global_hks[sc] = os['enter']
                            sc = (' %s:' % sc) if sc else '   '
                            widgets.append(Selectable(
                                urwid.Text([
                                    ('hotkey', sc), name,
                                    ('subtle', _friendly_count(tc)
                                               if (ti == 0) else '')]),
                                on_select=os))
                            if (ti == 0) and os and not shown:
                                self.default_action = def_act = os['enter']
                        shown.append(tag)
                if not shown:
                    pass  # FIXME: Add All Mail?  Add it anyway?

                count = 1
                unshown = [t for t in all_tags if t.lower() not in shown]
                if unshown:
                    widgets.append(urwid.Divider())
                    for ai, tag in enumerate(unshown):
                        action = _sel_search('in:%s' % tag, ctx_src_id)
                        sc = '   '
                        if count <= len(self.TAG_KEYS):
                            hk = self.TAG_KEYS[count-1]
                            sc = (' %s:' % hk)
                            self.global_hks[hk] = action
                        count += 1
                        name = tag[:1].upper() + tag[1:]
                        widgets.append(Selectable(
                            urwid.Text([('hotkey', sc), name]),
                            on_select={'enter': action}))
                        if ai == 0 and not def_act:
                            self.default_action = def_act = action
                    if count > 5:
                        pass  # FIXME: Add a "more" link, break loop
                widgets.append(urwid.Divider())

        configured = (len(widgets) > 0)
        if not configured and False:
            widgets.append(
                urwid.Text("""
This is moggie!

""", 'center'))

        if configured and False:  # FIXME
            widgets.append(urwid.Text([('subtle', '_'*20)], 'left', 'clip'))
            widgets.append(Selectable(urwid.Text(
                    [('hotkey', 'C:'), ('subtle', 'add context')], 'right'),
                on_select={'enter': lambda x: None}))

        self.walker[0:] = widgets
