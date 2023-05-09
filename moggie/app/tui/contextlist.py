import logging
import time
import urwid

from ...api.requests import RequestCommand
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
        ('outbox', (('9', 'OUTBOX',   'in:outbox'),)),
        ('sent',   (('0', 'Sent',     'in:sent'),)),
        ('spam',   (('s', 'Spam',     'in:spam'),)),
        ('trash',  (('d', 'Trash',    'in:trash'),))]
    TAG_KEYS = 'wertyu'

    def __init__(self, tui_frame, expanded=0):
        self.expanded = expanded
        self.tui_frame = tui_frame
        self.crumb = ''
        self.active = None
        self.walker = urwid.SimpleListWalker([])
        urwid.ListBox.__init__(self, self.walker)

        # FIXME: OK, this is where we should be calling the context
        #        and count CLI commands! In an async fashion!
        self.search_obj = RequestCommand('context', args=['--output=details'])
        self.counts_obj = RequestCommand('count')

        self.order = []
        self.contexts = {}
        self.tag_counted = 0
        self.tag_counts = {}
        self.update_content()
        self.awaiting_counts = {}

        # Configure event listeners, request a list of contexts.
        me = 'contextlist'
        cm = self.tui_frame.conn_manager
        cm.add_handler(me, '*', self.search_obj, self.incoming_contexts)
        cm.add_handler(me, '*', self.counts_obj, self.incoming_counts)
        cm.add_handler(me, '*', 'pong', self.incoming_pong)
        cm.send(self.search_obj)

    def expand(self, i):
        self.expanded = i
        self.update_content()

    def show_overview(self, i=None):
        pass  # FIXME

    def show_connections(self, i=None):
        pass  # FIXME

    def request_counts(self):
        self.tag_counted = time.time()
        self.awaiting_counts = {}
        for i, ctx in self.contexts.items():
            args = [
                '--multi',
                '--context=%s' % ctx['key']]
            all_tags = ctx.get('tags', []) + ctx.get('extra_tags', [])
            if all_tags:
                for tag in set(all_tags):
                    tag = tag.lower()
                    args.append('--q=in:%s' % tag)
                    args.append('--q=in:%s tag:unread' % tag)
                self.counts_obj.update({'args': args})
                self.awaiting_counts[self.counts_obj['req_id']] = i
                self.tui_frame.conn_manager.send(self.counts_obj)

    def incoming_contexts(self, source, message):
        logging.debug('Got contexts: %s' % message)
        for ctx, info in message['data'][0].items():
            full_ctx = '%s/%s' % (source, ctx)
            if full_ctx not in self.contexts:
                info['source'] = source
                self.order.append(full_ctx)
                self.contexts[full_ctx] = info
                self.tag_counts[full_ctx] = {}
            else:
                self.contexts[full_ctx].update(info)
        self.update_content()
        self.request_counts()

    def incoming_counts(self, source, message):
        ctx_id = self.awaiting_counts.get(message['req_id'])
        if ctx_id:
            self.tag_counts[ctx_id] = counts = {}
            for search, count in message['data'][0].items():
                search = search[3:].replace(' tag:unread', '*')
                counts[search] = count
            logging.debug('Counts for %s: %s' % (ctx_id, counts))
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
        def _sel_email(which):
            return lambda x: self.tui_frame.show_account(self.active, which)
        def _sel_search(terms):
            return lambda x: self.tui_frame.show_search_result(terms)

        def _friendly_count(tc):
            if tc < 1:
                return ''
            if tc < 1000:
                return ' %d' % tc
            if tc < 1000000:
                return ' %dk' % (tc // 1000)
            return 'oo'

        widgets = []
        last_ctx_name = '-:-!-:-'
        self.order.sort(
            key=lambda c: (0 if c.endswith('/Context 0') else 1, c))
        for i, ctx_id in enumerate(self.order):
            ctx = self.contexts[ctx_id]
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
                self.active = ctx
                self.crumb = ctx['name']
                widgets.append(Selectable(urwid.AttrMap(ctx_name,
                    {None: 'active', 'subtle': 'active', 'hotkey': 'act_hk'}),
                    on_select={'enter': self.show_overview}))
#               widgets.append(Selectable(urwid.Text(
#                   [('subtle', 'live:1')], 'right', 'clip'),
#                   on_select={'enter': self.show_connections}))
            else:
                widgets.append(Selectable(ctx_name,
                    on_select={'enter': _sel_ctx(i)}))

            if i == self.expanded:
                acount = 0
                for a_id, acct in ctx.get('accounts', {}).items():
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
                all_tags = ctx.get('tags', []) + ctx.get('extra_tags', [])
                for tag, items in self.TAG_ITEMS:
                    if tag in all_tags:
                        tc = self.tag_counts[ctx_id].get(tag.lower()+'*', 0)
                        for ti, (sc, name, search) in enumerate(items):
                            sc = (' %s:' % sc) if sc else '   '
                            os = search and {'enter': _sel_search(search)}
                            widgets.append(Selectable(
                                urwid.Text([
                                    ('hotkey', sc), name,
                                    ('subtle', _friendly_count(tc) if (ti == 0) else '')]),
                                on_select=os))
                        shown.append(tag)
                count = 1
                unshown = [t for t in all_tags if t not in shown]
                if unshown:
                    widgets.append(urwid.Divider())
                    for tag in unshown:
                        sc = '   '
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

        configured = (len(widgets) > 0)
        if not configured and False:
            widgets.append(
                urwid.Text("""
This is moggie!

""", 'center'))

        if configured:
            widgets.append(urwid.Text([('subtle', '_'*20)], 'left', 'clip'))
            widgets.append(Selectable(urwid.Text(
                    [('hotkey', 'C:'), ('subtle', 'add context')], 'right'),
                on_select={'enter': lambda x: None}))

        self.walker[0:] = widgets

