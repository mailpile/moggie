import copy
import logging
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class RetryDialog(MessageDialog):
    def __init__(self, tui, moggie, emsg, do_retry):
        self.moggie = moggie
        self.emsg = emsg
        self.doingit = False
        self.save_checkbox = None
        self.resource = emsg['exc_data'].get('resource')
        self.needed_info = emsg['exc_data'].get('need')
        self.do_retry = do_retry
        self.update = {}

        super().__init__(tui, emsg['error'])

    def make_buttons(self):
        return [
            CancelButton(lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Retry', lambda x: self.on_ok(), style='popsubtle')]

    def make_widgets(self):
        e_args = [
            ('allow_tab', False),
            ('wrap', 'clip'),
            ('multiline', False)]
        e_more = {
            'password': [('mask', '*')]}

        widgets = []
        for need in (self.needed_info or []):
            a = dict(e_args + e_more.get(need['datatype'], []))
            w = EditLine(need['label'] + ': ', **a)
            widgets.append(w)
            urwid.connect_signal(w, 'enter', lambda b: self.focus_next())
        widgets.append(urwid.Divider())

        if self.resource:
            self.save_checkbox = urwid.CheckBox(
                'Save password to configuration file', False)
            widgets.append(self.save_checkbox)
            widgets.append(urwid.Divider())

        return widgets

    def validate(self):
        valid = 0
        for i, widget in enumerate(self.widgets):
            if i >= len(self.needed_info):
                break
            need = self.needed_info[i]
            info = widget.edit_text.replace('\n', '')
            # FIXME: Actual validation? Give user feedback?
            if info:
                self.update[need['field']] = info
                valid += 1

        if self.save_checkbox and self.save_checkbox.get_state():
            self.update['remember_credentials'] = {
                self.resource: list(self.update.keys())}

        return valid

    def on_ok(self):
        completed = self.validate()
        if completed == len(self.needed_info):
            self._emit('close')
            self.do_retry(self.update)
