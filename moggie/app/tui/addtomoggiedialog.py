import os
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class AddToMoggieDialog(MessageDialog):
    HELP_TEXT = """\
Adding %d to [Context 0 / ... ]

  [ ] Watch for new messages and mailboxes
      [ ] Sync moggie tags and deletion back to server
  [ ] Copy messages to moggie's local storage
      [ ] Delete from source / server

  [ ] Use this account (foo@example.org) to send e-mail
      [ ] Copy sent mail to server

"""
    WANTED_HEIGHT = 4 + len(HELP_TEXT.splitlines())
    WANTED_WIDTH = 60

    signals = ['close']

    def __init__(self, tui, adding):
        self.adding = adding
        super().__init__(tui, message=self.HELP_TEXT)

    def make_buttons(self):
        return [
            CancelButton(lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Add', lambda x: self.doit(), style='popsubtle')]

    def make_widgets(self):
        self.path_box = EditLine('Path: ',
            multiline=False, allow_tab=False, wrap='ellipsis')
        urwid.connect_signal(
            self.path_box, 'enter', lambda b: self.validate(focus_next=True))


        return [self.path_box, urwid.Divider()]

    def validate(self, focus_next=False):
        return self.focus_next() if focus_next else True

    def doit(self):
        if self.validate():
            pass
