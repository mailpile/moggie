import logging
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class RetryDialog(MessageDialog):
    def __init__(self, tui_frame, emsg):
        self.emsg = emsg
        self.doingit = False
        self.needed_info = emsg['kwargs'].get('need')
        self.retry = emsg['request']

        super().__init__(tui_frame, emsg['error'])

    def make_buttons(self):
        return [
            CancelButton(lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Retry', lambda x: self.on_ok(), style='popsubtle')]

    def make_widgets(self):
        e_args = [
            ('allow_tab', False),
            ('wrap', 'clip'),
            ('multiline', True)]
        e_more = {
            'password': [('mask', '*')]}

        widgets = []
        for need in self.needed_info:
            a = dict(e_args + e_more.get(need['datatype'], {}))
            w = urwid.Edit(need['label'] + ': ', **a)
            widgets.append(w)
            urwid.connect_signal(w, 'change', lambda b,t: self.on_input(t))
        widgets.append(urwid.Divider())
        return widgets

    def validate(self):
        valid = 0
        for i, widget in enumerate(self.widgets):
            logging.debug('Validating %d/%s' % (i, widget))
            if i >= len(self.needed_info):
                break
            need = self.needed_info[i]
            info = widget.edit_text.replace('\n', '')
            # FIXME: Actual validation? Give user feedback?
            if info:
                self.retry[need['field']] = info
                valid += 1
        logging.debug('Validated %d fields' % valid)
        return valid

    def on_input(self, text):
        if '\n' in text:
            self.focus_next()
        return text.replace('\n', '')

    def on_ok(self):
        completed = self.validate()
        if completed == len(self.needed_info):
            self.tui_frame.conn_manager.send(self.retry)
            emit_soon(self, 'close')
