import base64
import logging
import os
import subprocess
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class OpenURLDialog(MessageDialog):
    WARNING = """
WARNING!
Links in e-mail can be treacherous. If you are asked
to log in or provide personal information, be aware
it may be a scam.
"""

    def __init__(self, tui, title, parent, url):
        self.parent = parent
        self.url = url
        super().__init__(tui, title='Open in Browser')

    def wanted_height(self):
        return 12 + (len(self.url) // (self.WANTED_WIDTH-3))

    def make_buttons(self):
        return [
            CancelButton(
                lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Open URL',
                lambda x: self.on_open(), style='popsubtle')]

    def make_widgets(self):
        return [
             urwid.Divider(),
             urwid.Text(self.url),
             urwid.Divider(),
             urwid.Text(self.WARNING.strip(), align='center')]

    def on_open(self):
        subprocess.Popen(['xdg-open', self.url])
        self._emit('close')

    def on_cancel(self):
        self.save_to = None
        self._emit('close')
