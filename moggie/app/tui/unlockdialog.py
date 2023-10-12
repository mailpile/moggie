import urwid

from .widgets import *


class UnlockDialog(urwid.WidgetWrap):
    HELP_TEXT = """\
Enter your passphrase (or password) to unlock the app.
"""
    WANTED_HEIGHT = 4 + len(HELP_TEXT.splitlines())
    WANTED_WIDTH = 60

    signals = ['close']

    def unlock(self, passphrase):
        passphrase = passphrase.replace('\n', '')
        if passphrase:
            self.tui.unlock(passphrase)
        self._emit('close')

    def __init__(self, tui):
        self.tui = tui

        self.unlock_box = EditLine('Passphrase: ',
            multiline=False, mask='*', allow_tab=False, wrap='ellipsis')
        urwid.connect_signal(self.unlock_box,
            'enter', lambda *e: self.unlock(self.unlock_box.edit_text))

        fill = urwid.Filler(urwid.Pile([
            self.unlock_box,
            urwid.Divider(),
            urwid.Text(('popsubtle', self.HELP_TEXT))]))
        super().__init__(urwid.LineBox(urwid.AttrWrap(fill, 'popbg')))
