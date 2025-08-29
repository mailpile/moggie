import logging
import urwid

from .widgets import *


class MessageDialog(urwid.WidgetWrap):
    WANTED_WIDTH = 60
    WANTED_HEIGHT = 8

    DEFAULT_OK = 'OK'

    signals = ['close']

    def __init__(self, tui, message='', title=''):
        self.tui = tui
        self.title = title

        self.close_button = CloseButton(
            lambda x: self._emit('close'), style='popsubtle')
        self.buttons = self.make_buttons()
        self.widgets = self.make_widgets()

        if message:
            self.message = self.wrap(message, self.WANTED_WIDTH-3) + '\n'
        else:
            self.message = None

        self.pile = urwid.Pile([])
        self.update_pile(title)

        super().__init__(urwid.LineBox(
            urwid.AttrWrap(urwid.Filler(self.pile), 'popbg')))

    def make_buttons(self):
        return [
            SimpleButton(self.DEFAULT_OK, lambda x: self.on_ok(),
            style='popsubtle')]

    def make_widgets(self):
        return []

    def update_pile(self, message='', widgets=False, buttons=False, focus=1):
        if buttons:
            self.buttons = self.make_buttons()
        if widgets:
            self.widgets = self.make_widgets()
        button_bar = [
            ('weight', 1, urwid.Text('')),
            ('weight', 1, urwid.Text(''))]
        for button in self.buttons:
            button_bar[1:1] = [('fixed', len(button.label)+2, button)]
        button_bar = urwid.Columns(button_bar, dividechars=1)
        button_bar.set_focus(1)

        message = message or self.title
        widgets = [urwid.Columns([
            ('weight', 1, urwid.Text(('status', '  %s  ' % message), 'center')),
            ('fixed',  3, self.close_button)])]
        widgets.extend(self.widgets)
        if self.message:
            widgets.append(urwid.Text(('popsubtle', self.message)))
        else:
            widgets.append(urwid.Divider())
        widgets.append(button_bar)

        self.pile.contents = ([(w, ('pack', None)) for w in widgets])
        self.focus_next(first=focus)
        self.tui.redraw()

    def wanted_height(self):
        return (5 +
            len((self.message or '').splitlines()) +
            len(self.widgets))

    def on_ok(self):
        self._emit('close')

    def focus_next(self, first=None):
        if first is None:
            current_pos = self.pile.focus_position
        else:
            current_pos = first - 1
        for i, w in enumerate(self.pile.contents):
            if ((i > current_pos)
                    and hasattr(w[0], 'selectable')
                    and w[0].selectable()):
                try:
                    self.pile.set_focus(i)
                    return True
                except IndexError:
                    pass
        return False

    def focus_last(self):
        last_focusable = None
        for i, w in enumerate(self.pile.contents):
            if (hasattr(w[0], 'selectable') and w[0].selectable()):
                try:
                    last_focusable = i
                except IndexError:
                    pass
        if last_focusable is None:
            return False
        self.pile.set_focus(last_focusable)
        return True

    def wrap(self, txt, maxwidth=(WANTED_WIDTH-3)):
        lines = []
        words = txt.split()
        for word in words:
            if not lines or (len(word) + len(lines[-1])) > maxwidth:
                lines.append(word)
            else:
                lines[-1] += ' ' + word
        return '\n'.join(lines)
