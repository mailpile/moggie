import base64
import logging
import os
import subprocess
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class SaveOrOpenDialog(MessageDialog):
    DEFAULT_TARGET = '~/Downloads'

    def __init__(self, tui_frame, title, parent, part, filename, target=None):
        self.parent = parent
        self.part = part
  
        filename = os.path.basename(filename)
        self.filename = filename
        self.target = os.path.join(target or self.DEFAULT_TARGET, filename)

        self.save_dest_input = None
        self.save_to = None
        self.want_open = False

        super().__init__(tui_frame,
            message='Note: The directory must already exist.')

    def make_buttons(self):
        return [
            CancelButton(
                lambda x: self._emit('close'), style='popsubtle'),
            SimpleButton('Save',
                lambda x: self.on_save(), style='popsubtle'),
            SimpleButton('Save and Open',
                lambda x: self.on_open(), style='popsubtle')]

    def make_widgets(self):
        self.save_dest_input = EditLine('Save to: ',
            wrap='clip',
            allow_tab=False,
            multiline=False)

        self.save_dest_input.edit_text = self.target
        urwid.connect_signal(
            self.save_dest_input, 'enter', lambda b: self.focus_next())

        return [
            self.save_dest_input,
            urwid.Divider()]

    def validate(self):
        dest_file = os.path.expanduser(self.save_dest_input.edit_text)
        dest_dir = os.path.dirname(dest_file)
        if not os.path.exists(dest_dir):
            self.update_pile(message='Directory does not exist!')
            return False
        if os.path.exists(dest_file):
            self.update_pile(message='File already exists!')
            return False
        return dest_file

    def on_save(self):
        self.save_to = self.validate()
        if self.save_to:
            self.widgets[0] = urwid.Text('Saving to %s' % self.save_to)
            self.buttons = [
                SimpleButton('Downloading, click to abort',
                    lambda x: self.on_cancel(), style='popsubtle')]
            self.update_pile()
            if '_DATA' in self.part:
                self.on_downloaded()
            else:
                self.parent.get_data(self.part, callback=self.on_downloaded)
        return False

    def on_open(self):
        self.want_open = True
        return self.on_save()

    def on_downloaded(self):
        if not self.save_to:
            return
        if '_DATA' in self.part:
            try:
                with open(self.save_to, 'wb') as fd:
                    fd.write(base64.b64decode(self.part['_DATA']))
                if self.want_open:
                    subprocess.Popen(['xdg-open', self.save_to])
                self._emit('close')
                return True
            except (OSError, IOError) as e:
                self.update_pile(message=str(e))
        else:
            self.update_pile(message='Need attachment data')

    def on_cancel(self):
        self.save_to = None
        self._emit('close')
