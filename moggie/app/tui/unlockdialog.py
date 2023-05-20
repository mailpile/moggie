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
        if '\n' in passphrase:
            passphrase = passphrase.replace('\n', '')
            if passphrase:
                self.tui_frame.unlock(passphrase)
            self._emit('close')

    def __init__(self, tui_frame):
        self.tui_frame = tui_frame

        self.unlock_box = urwid.Edit('Passphrase: ',
            multiline=True, mask='*', allow_tab=False, wrap='ellipsis')
        urwid.connect_signal(
            self.unlock_box, 'change', lambda b,t: self.unlock(t))

        fill = urwid.Filler(urwid.Pile([
            self.unlock_box,
            urwid.Divider(),
            urwid.Text(('popsubtle', self.HELP_TEXT))]))
        super().__init__(urwid.LineBox(urwid.AttrWrap(fill, 'popbg')))
