import urwid

from ...jmap.requests import RequestContexts, RequestCounts
from .widgets import *


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
        self.crumb = ''
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
                all_tags = ctx.get('tags', []) + ctx.get('extra_tags', [])
                for tag, items in self.TAG_ITEMS:
                    if tag in all_tags:
                        for sc, name, search in items:
                            sc = (' %s:' % sc) if sc else '   '
                            os = search and {'enter': _sel_search(search)}
                            widgets.append(Selectable(
                                urwid.Text([('hotkey', sc), name]),
                                on_select=os))
                        shown.append(tag)
                count = 1
                unshown = [t for t in all_tags if t not in shown]
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
        # FIXME: Need to update listener logic to listen for any and all
        #        count results...
        for i, ctx in enumerate(self.contexts):
            self.counts_obj['context'] = ctx['key']
            self.counts_obj['terms_list'] = count_terms = []
            if i == self.expanded:
                all_tags = ctx.get('tags', []) + ctx.get('extra_tags', [])
                for tag in set(all_tags):
                    count_terms.append('in:%s' % tag)
                    count_terms.append('in:%s tag:unread' % tag)
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
            # FIXME: Do something with the counts!
            self.update_content()

        # FIXME: The backend should broadcast updates...
