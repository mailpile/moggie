import logging
import os
import urwid

from .widgets import *
from .messagedialog import MessageDialog
from .quickcomposedialog import QuickComposeDialog
from ...email.addresses import AddressInfo


class HeaderActionDialog(MessageDialog):
    def __init__(self, tui, title, message, parent, metadata, fkey, value):
        self.parent = parent
        self.metadata = metadata

        self.hdr_key = fkey
        self.hdr_value = value
        self.hdr_actions = [
            ('Compose', self.make_composers),
            ('Search', self.make_searches)]

        if title and (len(title) > 40):
            title = title[:38] + '..'

        super().__init__(tui, title=title, message=message)

    def make_buttons(self):
        return [
            CancelButton(
                lambda x: self._emit('close'), style='popsubtle')]

    def make_widgets(self):
        widgets = []

        def _sect(label):
            widgets.extend([
                urwid.Divider(),
                urwid.Text(label + ':')])

        def _opt(hotkey, label, action):
            widgets.append(Selectable(
                urwid.Text(['  ',
                    ('go_hotkey', (hotkey+': ') if hotkey else '   '),
                    ('go_desc', label)]),
                on_select={'enter': action}))

        for title, actions in self.hdr_actions:
            actions = actions()
            if not actions:
                continue
            _sect(title)
            for hotkey, title, action in actions:
                _opt(hotkey, title, action)

        return widgets

    def make_searches(self):
        searches = []

        first = True
        if self.hdr_key == 'subject':
            # FIXME: Use same key extraction logic as message parser,
            #        only search for two-three most interesting keywords?
            terms = ' AND '.join(
                'subject:' + w.lower()
                for w in self.hdr_value.split()
                if len(w) > 3)
            searches.append((
                's' if first else None,
                'Search for similar subject lines',
                lambda x: self.on_search(terms)))
            first = False

        elif self.hdr_key in ('from', 'to', 'cc', 'bcc'):
            addresses = self.hdr_value
            if isinstance(addresses, dict):
                addresses = [addresses]
                
            for addr in addresses:
                addr = AddressInfo(**addr).address
                searches.append((
                    's' if first else None,
                    'Search by e-mail: ' + addr,
                    lambda x: self.on_search('email:' + addr)))
                first = False

        return searches

    def make_composers(self):
        composers = []

        composers.extend([(
            'a', 'Reply to message (reply-all)',
            lambda x: self.on_compose(None, True)
            )])

        first = True
        if self.hdr_key in ('from', 'reply-to', 'to', 'cc', 'bcc'):
            addresses = self.hdr_value
            if isinstance(addresses, dict):
                addresses = [addresses]

            for addr in addresses:
                ai = AddressInfo(**addr)
                addr = ai.friendly(max_width=30, only_address=True) # FIXME: Magic number
                composers.extend([(
                    'r' if first else None,
                    'Reply directly to: ' + addr,
                    lambda x: self.on_compose([str(ai)], True)
                ),(
                    'c' if first else None,
                    'Compose e-mail to: ' + addr,
                    lambda x: self.on_compose([str(ai)], False))])
                first = False

        return composers

    def keypress(self, size, key):
        for title, group in self.actions:
            for hotkey, hint, action in group:
                if hotkey == key:
                    action(None)
                    return None

        return super().keypress(size, key)

    def validate(self):
        return True

    def on_search(self, terms):
        self.tui.show_search_result(self.parent.mog_ctx, terms)
        self._emit('close')
        return False

    def on_compose(self, recipients, as_reply):
        self._emit('close')
        self.tui.show_modal(QuickComposeDialog,
            recipients, self.metadata if as_reply else None)
        return False

    def on_cancel(self):
        self._emit('close')
