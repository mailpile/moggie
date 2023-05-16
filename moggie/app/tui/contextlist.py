import logging
import time
import urwid

from ...api.requests import RequestCommand
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
        ('spam',   (('s', 'Spam',     'in:spam'),)),
        ('trash',  (('d', 'Trash',    'in:trash'),))]
    TAG_KEYS = 'wertyu'

    def __init__(self, tui_frame, update_parent=None, expanded=0, first=False):
        self.expanded = expanded
        self.tui_frame = tui_frame
        self.update_parent = update_parent or (lambda: None)
        self.first = first
        self.crumb = ''
        self.active = None
        self.default_action = None
        self.walker = urwid.SimpleListWalker([])
        self.v_history = []
        self.v_history_max = 4
        self.hotkeys = {}

        self.order = []
        self.contexts = {}
        self.tag_counted = 0
        self.tag_counts = {}
        self.update_content()
        self.awaiting_counts = {}

        self.search_obj = RequestCommand('context', args=['--output=details'])
        self.counts_obj = RequestCommand('count')

        urwid.ListBox.__init__(self, self.walker)

        # Configure event listeners, request a list of contexts.
        me = 'contextlist'
        cm = self.tui_frame.conn_manager
        cm.add_handler(me, '*', self.search_obj, self.incoming_contexts)
        cm.add_handler(me, '*', self.counts_obj, self.incoming_counts)
        cm.add_handler(me, '*', 'pong', self.incoming_pong)
        cm.send(self.search_obj)

    def expand(self, i, activate=True):
        self.expanded = i
        self.update_content()
        if activate:
            self.activate_default_view()

    def activate_default_view(self):
        if self.default_action is not None:
            self.default_action(None)
            self.first = False
            return True
        self.first = True
        return False

    def _get_context(self, context):
        if context is None:
            context = self.active
        if isinstance(context, str):
            if '/' not in context:
                context = 'local_app/' + context
            context = self.contexts[context]
        return context

    def get_context_and_src_ids(self, context=None):
        try:
            context = self._get_context(context)
            return context['key'], context['ctx_src_id']
        except (KeyError, TypeError):
            return 'Context 0', 'local_app/Context 0'

    def send_with_context(self, message_obj, context=None):
        if self.contexts:
            context = self._get_context(context)

            if isinstance(message_obj, dict):
                message_obj['context'] = context['key']
            if isinstance(message_obj, RequestCommand):
                context_arg = ['--context=%s' % context['key']]
                if message_obj['args'][:1] != context_arg:
                    message_obj['args'][:0] = context_arg

            bridge = context['source']
        else:
            bridge = None

        self.tui_frame.conn_manager.send(message_obj, bridge_name=bridge)

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

    def show_connections(self, context):
        logging.debug('FIXME: Should show context connections')

    def show_account(self, account):
        logging.debug('FIXME: Should show account details: %s' % account)

    def show_search(self, terms, ctx_src_id):
        return self.tui_frame.show_search_result(terms, ctx_src_id,
            history=False)

    def request_counts(self):
        self.tag_counted = time.time()
        self.awaiting_counts = {}
        for ctx_src_id, ctx in self.contexts.items():
            args = ['--multi']
            all_tags = ctx.get('tags', []) + ctx.get('extra_tags', [])
            if all_tags:
                for tag in set(all_tags):
                    tag = tag.lower()
                    args.append('--q=in:%s' % tag)
                    args.append('--q=in:%s tag:unread' % tag)
                self.counts_obj.update({'args': args})
                self.awaiting_counts[self.counts_obj['req_id']] = ctx_src_id
                self.send_with_context(self.counts_obj, ctx)

    def incoming_contexts(self, source, message):
        for ctx, info in message['data'][0].items():
            ctx_src_id = '%s/%s' % (source, ctx)
            if ctx_src_id not in self.contexts:
                info['source'] = source
                info['ctx_src_id'] = ctx_src_id
                self.order.append(ctx_src_id)
                self.contexts[ctx_src_id] = info
                self.tag_counts[ctx_src_id] = {}
            else:
                self.contexts[ctx_src_id].update(info)
        self.update_content()
        self.request_counts()
        self.update_parent()
        if self.first:
            logging.debug('Launching default action?')
            self.activate_default_view()

    def incoming_counts(self, source, message):
        ctx_src_id = self.awaiting_counts.get(message['req_id'])
        if ctx_src_id and message.get('data'):
            self.tag_counts[ctx_src_id] = counts = {}
            for search, count in message['data'][0].items():
                search = search[3:].replace(' tag:unread', '*')
                counts[search] = count
            self.update_content()

    def incoming_pong(self, source, message):
        # FIXME: We should put in some better plumbing, so the backend
        #        app notifies us proactively when counts change. And
        #        especially if the config changes, which since there can
        #        be multiple front-ends, is bound to happen!
        if self.tag_counted < time.time() - self.COUNT_INTERVAL:
            self.request_counts()

    def update_content(self):
        def _sel_ctx(which):
            # This goes through the main frame in case it wants to know
            # things have changed and coordinate with other UI elements.
            return lambda x: self.tui_frame.set_context(which)
        def _sel_email(account):
            return lambda x: self.show_account(account)
        def _sel_search(terms, ctx_src_id):
            return lambda x: self.show_search(terms, ctx_src_id)
        def _sel_mailbox(path, ctx_src_id):
            return lambda x: self.tui_frame.show_mailbox(
                path, ctx_src_id, history=False)
        def _sel_history(*args):
            return lambda x: self.show_history(*args)

        def _friendly_count(tc):
            if tc < 1:
                return ''
            if tc < 1000:
                return ' %d' % tc
            if tc < 1000000:
                return ' %dk' % (tc // 1000)
            return 'oo'

        self.hotkeys = {}
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
            ctx = self.contexts[ctx_src_id]
            name = ctx['name']
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
                self.active = ctx
                self.crumb = ctx['name']
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
                for ai, (a_id, acct) in enumerate(
                        ctx.get('accounts', {}).items()):
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
                all_tags = ctx.get('tags', []) + ctx.get('extra_tags', [])
                for tag, items in self.TAG_ITEMS:
                    all_lc_tags = [t.lower() for t in all_tags]
                    if tag in all_lc_tags:
                        tc = self.tag_counts[ctx_src_id].get(tag.lower()+'*', 0)
                        for ti, (sc, name, search) in enumerate(items):
                            os = search and {'enter': _sel_search(
                                search, ctx_src_id)}
                            if sc and os:
                                self.hotkeys[sc] = os['enter']
                            sc = (' %s:' % sc) if sc else '   '
                            widgets.append(Selectable(
                                urwid.Text([
                                    ('hotkey', sc), name,
                                    ('subtle', _friendly_count(tc) if (ti == 0) else '')]),
                                on_select=os))
                            if (ti == 0) and os and not shown:
                                self.default_action = def_act = os['enter']
                        shown.append(tag)

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
                            self.hotkeys[hk] = action
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

