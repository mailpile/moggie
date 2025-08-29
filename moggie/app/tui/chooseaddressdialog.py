import logging
import os
import re
import urwid

from .widgets import *
from .messagedialog import MessageDialog
from ...email.addresses import AddressInfo, AddressHeaderParser


class ChooseAddressDialog(MessageDialog):
    HELP_TEXT = """
Type to search for e-mail addresses.
"""
    WANTED_HEIGHT = 15 + len(HELP_TEXT.splitlines())
    WANTED_WIDTH = 60

    signals = ['close']

    def __init__(self, tui, mog_ctx,
            which, field, initial_addr, chosen_callback,
            editline=EditLine,
            others={}):

        self.mog_ctx = mog_ctx
        self.made_choice = False
        self.chosen_callback = chosen_callback
        self.addresses = []
        self.address_box = None
        self.editline = editline
        
        self.which = which
        self.others = others or {}
        self.initial_address = initial_addr 

        super().__init__(tui, title='E-mail Address (%s)' % field)

        if initial_addr:
            self.validate()

    def make_buttons(self):
        c = self.cancel_button = CancelButton(
            lambda x: self._emit('close'), style='popsubtle')
        u = self.use_button = SimpleButton('Use This',
            lambda x: self.done(), style='popsubtle')
        if self.initial_address:
            def mk_add(which=None):
                return lambda x: self.done(add=True, _as=which)
            a = self.add_button = SimpleButton(
                'Add', mk_add(), style='popsubtle')
            o = self.other_buttons = [
                    SimpleButton('Add ' + v, mk_add(k), style='popsubtle')
                    for k, v in reversed(self.others.items())]
            r = self.remove_button = SimpleButton('Remove',
                lambda x: self.done(remove=True), style='popsubtle')
            return [c, r] + o + [a, u]
        else:
            return [c, u]

    def make_widgets(self):
        if not self.address_box:
            self.address_box = self.editline('E-mail: ',
                multiline=False, allow_tab=False, wrap='ellipsis')
            self.address_box.edit_text = self.initial_address

            urwid.connect_signal(
                self.address_box, 'change', lambda a,b: self.update_search())
            urwid.connect_signal(
                self.address_box, 'enter', lambda b: self.validate(focus_last=True))

        def _mk_cb(addr):
            return lambda *a: self.select_address(addr)

        top_ten = []
        for addr in self.addresses:
            if addr['score']:
                top_ten.append(Selectable(
                    urwid.Text([' * ', ('go_desc', addr['name-addr'])],
                        wrap='clip'),
                    on_select={
                        'enter': _mk_cb(addr)}))
        if not top_ten:
            top_ten.append(urwid.Text(self.HELP_TEXT.strip()))

        return [self.address_box, urwid.Divider()] + top_ten + [urwid.Divider()]

    def select_address(self, addr):
        self.made_choice = True
        self.address_box.edit_text = addr['name-addr']
        self.focus_last()

    def update_search(self):
        if self.made_choice:
            self.made_choice = False
            return

        search_terms = self.address_box.edit_text
        if len(search_terms) > 2:
            terms = search_terms.replace('@', ' ').strip() + '*'
            args = terms.split() + [
                '--output=recipients',
                '--output=sender',
                '--output=count',
                '--output=score',
                '--deduplicate=address']
            self.mog_ctx.address(*args, on_success=self.process_search_results)

    def process_search_results(self, mog_ctx, search_result):
        if isinstance(search_result, list):
            search_result.sort(key=lambda r: (-r['score'], -r['count']))
            self.addresses = search_result[:10]
            self.update_pile(widgets=True)

    def validate(self, focus_last=False):
        addr = self.address_box.edit_text.strip()
        ai = AddressHeaderParser(addr)
        if len(ai) < 1:
            self.update_pile(message='Address invalid')
            return False
        elif len(ai) > 1:
            self.update_pile(message='Too many addresses!')
            return False
        elif focus_last:
            self.update_pile(message='')
            return self.focus_last()
        else:
            self.update_pile(message='')
            return ai[0].friendly(max_width=None)

    def done(self, remove=False, add=False, _as=None):
        addr = self.validate()
        if addr:
            logging.debug('Chose address: %s' % addr)
            self.chosen_callback(
                _as or self.which, addr, self.initial_address, remove, add)
            self._emit('close')
