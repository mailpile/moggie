import base64
import logging
import os
import subprocess
import urwid
import threading

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
        super().__init__(tui, title='Open URL')

    def wanted_height(self):
        return 12 + (len(self.url) // (self.WANTED_WIDTH-3))

    def make_buttons(self):
        return [
            CancelButton(
                lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Clipboard',
                lambda x: self.on_clipboard(), style='popsubtle'),
            SimpleButton('Open in Browser',
                lambda x: self.on_browser(), style='popsubtle')]

    def make_widgets(self):
        return [
             urwid.Divider(),
             urwid.Text(self.url),
             urwid.Divider(),
             urwid.Text(self.WARNING.strip(), align='center')]

    def on_clipboard(self):
        xclip = subprocess.Popen(['xclip', '-selection', 'c', '-silent'],
            stdin=subprocess.PIPE)
        xclip.stdin.write(bytes(self.url, 'utf-8'))
        xclip.stdin.close()

        reaper = threading.Thread(target=xclip.wait)
        reaper.daemon = True
        reaper.start()

        self._emit('close')

    def on_browser(self):
        subprocess.Popen(['xdg-open', self.url])
        self._emit('close')

    def on_cancel(self):
        self.save_to = None
        self._emit('close')
