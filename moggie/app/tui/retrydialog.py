import logging
import urwid

from .widgets import *


class RetryDialog(urwid.WidgetWrap):
    WANTED_WIDTH = 60
    WANTED_HEIGHT = 8

    signals = ['close']

    def validate(self):
        valid = 0 
        for i, widget in enumerate(self.needed_widgets):
            need = self.needed_info[i]
            info = widget.edit_text.replace('\n', '')
            # FIXME: Actual validation? Give user feedback?
            if info:
                self.retry[need['field']] = info
                valid += 1
        return valid

    def wanted_height(self):
        return (4 + 
            (len(self.needed_info)) +
            (len(self.error_message) // (self.WANTED_WIDTH-20)))

    def on_input(self, text):
        if '\n' in text:
            self.pile.focus_position += 1
        return text.replace('\n', '')

    def on_ok(self):
        completed = self.validate()
        if completed == len(self.needed_info):
            self.tui_frame.conn_manager.send(self.retry)
            emit_soon(self, 'close')

    def update_pile(self, message='', focus=0):
        widgets = [
            urwid.Columns([
                ('weight', 1, self.needed_widgets[0]),
                ('fixed',  3, self.close_button)])
        ] + self.needed_widgets[1:] + [
            urwid.Columns([
                ('weight', 1, urwid.Text('')),
                ('fixed', len('Retry')+2, self.retry_button),
                ('fixed', len('Cancel')+2, self.cancel_button),
                ('weight', 1, urwid.Text(''))]),
            urwid.Text(('status', message), 'center'),
            urwid.Text(('popsubtle', self.error_message))]

        #    urwid.connect_signal(w, 'change', lambda b,t: self.doit(t))

        self.pile.contents = ([(w, ('pack', None)) for w in widgets])
        self.pile.focus_position = focus

    def __init__(self, tui_frame, emsg):
        self.tui_frame = tui_frame
        self.doingit = False

        self.close_button = CloseButton(lambda x: self._emit('close'))
        self.cancel_button = CancelButton(lambda x: self._emit('close'))
        self.retry_button = SimpleButton('Retry', lambda x: self.on_ok())

        self.error_message = emsg['error']
        self.needed_info = emsg['kwargs'].get('need')
        self.retry = emsg['request']

        e_args = [
            ('allow_tab', False),
            ('wrap', 'clip'),
            ('multiline', True)]
        e_more = {
            'password': [('mask', '*')]}

        self.needed_widgets = []
        for need in self.needed_info:
            a = dict(e_args + e_more.get(need['datatype'], {}))
            w = urwid.Edit(need['label'] + ': ', **a)
            self.needed_widgets.append(w)
            urwid.connect_signal(w, 'change', lambda b,t: self.on_input(t))

        self.pile = urwid.Pile([])
        self.update_pile()

        super().__init__(urwid.LineBox(
            urwid.AttrWrap(urwid.Filler(self.pile), 'popbg')))
