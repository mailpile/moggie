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
            ('multiline', False)]
        e_more = {
            'password': [('mask', '*')]}

        widgets = []
        for need in self.needed_info:
            a = dict(e_args + e_more.get(need['datatype'], []))
            w = EditLine(need['label'] + ': ', **a)
            widgets.append(w)
            urwid.connect_signal(w, 'enter', lambda b: self.focus_next())
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

    def on_ok(self):
        completed = self.validate()
        if completed == len(self.needed_info):
            self._emit('close')
            self.tui_frame.conn_manager.send(self.retry)
