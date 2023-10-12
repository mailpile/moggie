import os
import re
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class BrowsePathDialog(MessageDialog):
    HELP_TEXT = """\
Enter the /path/to/a/directory you would like to
browse, or an IMAP URI (imap://username@imap.example.org/).
"""
    WANTED_HEIGHT = 4 + len(HELP_TEXT.splitlines())
    WANTED_WIDTH = 60

    signals = ['close']

    def __init__(self, tui):
        super().__init__(tui, message=self.HELP_TEXT)

    def make_buttons(self):
        return [
            CancelButton(
                lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Open',
                lambda x: self.open(), style='popsubtle')]

    def make_widgets(self):
        self.path_box = EditLine('Path: ',
            multiline=False, allow_tab=False, wrap='ellipsis')
        urwid.connect_signal(
            self.path_box, 'enter', lambda b: self.validate(focus_next=True))

        return [self.path_box, urwid.Divider()]

    def validate(self, focus_next=False):
        path = self.path_box.edit_text.strip()
        if path.startswith('imap:'):
            self.update_pile(message='')
            return self.focus_next() if focus_next else path
        if not path:
            return False
           
# FIXME: Add an API endpoint to the backend which lets us request
#        auto-detection of IMAP servers based on e-mail address.
#
#       if '/' not in path and '@' in path:
#           user, domain = path.split('@')
#           self.update_pile(message='Attempting auto-detection...')
#           return False

        path = os.path.expanduser(path)
        if not os.path.exists(path):
                
            self.update_pile(message='No such file or directory!')
            return False 

        self.update_pile(message='')
        return self.focus_next() if focus_next else path

    def open(self):
        pathname = self.validate()
        if pathname:
            self.tui.show_browser(pathname)
            self._emit('close')
