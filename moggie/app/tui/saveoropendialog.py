import base64
import logging
import os
import subprocess
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class SaveOrOpenDialog(MessageDialog):
    DEFAULT_TARGET = '~/Downloads'

    SUPPORTED_EXTENSIONS = set(['.eml', '.mbx', '.mdz'])

    def __init__(self, tui, title, parent, part, filename, target=None):
        self.parent = parent
        self.part = part

        filename = os.path.basename(filename)
        extension = os.path.splitext(filename)[-1]

        self.filename = filename
        self.target = os.path.join(target or self.DEFAULT_TARGET, filename)

        self.overwrite = None
        self.create_dirs = None
        self.open_in_moggie = None

        self.save_dest_input = None
        self.save_to = None
        self.want_open = False

        if extension in self.SUPPORTED_EXTENSIONS:
            self.open_in_moggie = urwid.CheckBox('Open in moggie', True)
            message = None
        else:
            message = 'Note: The directory must already exist.'

        super().__init__(tui, message=message)

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

        widgets = [self.save_dest_input, urwid.Divider()]

        if self.overwrite is not None:
            widgets.append(self.overwrite)
        if self.create_dirs is not None:
            widgets.append(self.create_dirs)
        if self.open_in_moggie is not None:
            widgets.append(self.open_in_moggie)

        return widgets

    def validate(self):
        dest_file = os.path.expanduser(self.save_dest_input.edit_text)
        dest_dir = os.path.dirname(dest_file)

        create_dirs = self.create_dirs and self.create_dirs.get_state()
        if not os.path.exists(dest_dir):
            message = 'Directory does not exist!'
            if create_dirs:
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                except OSError as e:
                    create_dirs = False
                    message = '%s' % e

            if not create_dirs:
                if self.create_dirs is None:
                    self.message = None
                    self.create_dirs = urwid.CheckBox(
                        'Create parent directories', False)
                    self.widgets.append(self.create_dirs)
                self.update_pile(message=message)
                return False

        overwrite = self.overwrite and self.overwrite.get_state()
        if os.path.exists(dest_file) and not overwrite:
            if self.overwrite is None:
                self.message = None
                self.overwrite = urwid.CheckBox(
                    'Overwrite existing file', False)
                self.widgets.append(self.overwrite)
            self.update_pile(message='File already exists!')
            return False

        return dest_file

    def open_attachment(self):
        internal = self.open_in_moggie and self.open_in_moggie.get_state()
        if internal:
            logging.error(
                'FIXME: Create a search pane displaying the created file')
        else:
            subprocess.Popen(['xdg-open', self.save_to])

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
                    self.open_attachment()
                self.update_pile(message='Saved!')
                emit_soon(self, 'close', seconds=1)
                return True
            except (OSError, IOError) as e:
                self.update_pile(message=str(e))
        else:
            self.update_pile(message='Need attachment data')

    def on_cancel(self):
        self.save_to = None
        self._emit('close')
