import logging
import urwid

from .widgets import *


class MessageDialog(urwid.WidgetWrap):
    WANTED_WIDTH = 60
    WANTED_HEIGHT = 8

    signals = ['close']

    def __init__(self, tui_frame, message):
        self.tui_frame = tui_frame
        self.doingit = False

        self.close_button = CloseButton(lambda x: self._emit('close'))
        self.buttons = self.make_buttons()
        self.widgets = self.make_widgets()

        self.message = self.wrap(message, self.WANTED_WIDTH-3)

        self.pile = urwid.Pile([])
        self.update_pile()

        super().__init__(urwid.LineBox(
            urwid.AttrWrap(urwid.Filler(self.pile), 'popbg')))

    def make_buttons(self):
        return [SimpleButton('OK', lambda x: self.on_ok())]

    def make_widgets(self):
        return []

    def update_pile(self, message='', focus=1):
        button_bar = [
            ('weight', 1, urwid.Text('')),
            ('weight', 1, urwid.Text(''))]
        for button in self.buttons:
            button_bar[1:1] = [('fixed', len(button.label)+2, button)]
        button_bar = urwid.Columns(button_bar, dividechars=1)
        button_bar.set_focus(1)

        widgets = [
            urwid.Columns([
                ('weight', 1, urwid.Text(('status', message), 'center')),
                ('fixed',  3, self.close_button)])
        ] + self.widgets + [
            urwid.Text(('popsubtle', self.message)),
            button_bar]

        self.pile.contents = ([(w, ('pack', None)) for w in widgets])
        self.focus_next(first=focus)

    def wanted_height(self):
        return len(self.message.splitlines()) + len(self.widgets) + 5

    def on_ok(self):
        emit_soon(self, 'close')

    def focus_next(self, first=None):
        if first is None:
            current_pos = self.pile.focus_position
        else:
            current_pos = first - 1
        for i, w in enumerate(self.pile.contents):
            if (i > current_pos) and hasattr(w[0], 'keypress'):
                try:
                    self.pile.set_focus(i)
                    logging.debug('Focused %s ?' % w[0])
                    return True
                except IndexError:
                    pass
        return False

    def wrap(self, txt, maxwidth=(WANTED_WIDTH-3)):
        lines = []
        words = txt.split()
        for word in words:
            if not lines or (len(word) + len(lines[-1])) > maxwidth:
                lines.append(word)
            else:
                lines[-1] += ' ' + word
        return '\n'.join(lines)
