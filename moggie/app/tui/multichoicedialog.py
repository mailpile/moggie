import os
import re
import urwid

from .widgets import *
from .messagedialog import MessageDialog


class MultiChoiceDialog(MessageDialog):
    WANTED_HEIGHT = 4
    WANTED_WIDTH = 70

    signals = ['close']

    def __init__(self, tui, choices,
            title=None, multi=False, action=None, default=None,
            prompt='Value', create=False, ok_labels=None, allow_none=False):
        self.title = title
        self.multi = multi
        self.default = default
        self.defaults = set(i.strip() for i in (default or '').split(','))
        self.choices = choices
        self.checkboxes = {}
        self.prompt = prompt
        self.create = create
        self.ok_labels = ok_labels or [self.DEFAULT_OK]
        self.action = action
        self.allow_none = 'None' if (allow_none is True) else allow_none
        super().__init__(tui, title=title)

    def action(self, result):
        raise RuntimeError('No action specified')

    def make_buttons(self):
        style = {'style': 'popsubtle'}
        buttons = [
            CancelButton(lambda x: self._emit('close'), **style)]
        if (self.create or
                self.multi or
                self.allow_none or
                (len(self.ok_labels) > 1)):
            for ok in reversed(self.ok_labels):
                def mk_cb(which):
                    return lambda x: self.ok(which)
                buttons.append(SimpleButton(ok, mk_cb(ok), **style))
        if self.allow_none:
            buttons[1:1] = [
                SimpleButton(self.allow_none, lambda x: self.none(), **style)]
        return buttons

    def make_widgets(self):
        self.input = EditLine('%s: ' % self.prompt,
            multiline=False, allow_tab=False, wrap='ellipsis')
        self.input.edit_text = self.default or ''
        urwid.connect_signal(
            self.input, 'enter', lambda b: self.validate(focus_next=True))

        width = 12
        madd = 4 if self.multi else 0
        for choice in self.choices:
            width = max(width, len(choice) + madd)
        width = min(width, self.WANTED_WIDTH - 4)
        columns = max(1, (self.WANTED_WIDTH - 4) // width)
        rows = len(self.choices) // columns
        if rows * columns < len(self.choices):
            rows += 1
        padding = (self.WANTED_WIDTH - (columns * width) - 2) // 2

        widgets = []
        has_cb = set()
        self.checkboxes = {}
        if self.choices:
            widgets.append(urwid.Divider())
            def _sc(c):
                return (lambda ignored: self.set_choice(c))
            data = [[''] * columns for row in range(0, rows)]

            for i, choice in enumerate(self.choices):
                data[i // columns][i % columns] = choice

            def elem(choice):
                if not choice:
                    return urwid.Text('')
                elif self.multi:
                    is_checked = choice in self.defaults
                    cb = self.checkboxes[choice] = urwid.CheckBox(
                        ('popsubtle', choice), is_checked)
                    if is_checked:
                        has_cb.add(choice)
                    return cb
                else:
                    return Selectable(
                        urwid.Text(('popsubtle', choice),
                            align='left', wrap='clip'),
                        on_select={'enter': _sc(choice)})

            widgets.extend([
                urwid.Padding(urwid.Columns([
                    ('fixed', width, elem(choice))
                    for choice in row]), left=padding)
                for row in data])

        if has_cb:
            others = ','.join(sorted(list(self.defaults - has_cb)))
            self.input.edit_text = others

        if self.create:
            if widgets:
                widgets.append(urwid.Divider())
            widgets.append(self.input)

        return widgets

    def normalize(self, value):
        return value.strip().lower()

    def set_choice(self, tag):
        self.input.edit_text = self.normalize(tag)
        if not self.create and (len(self.ok_labels) == 1):
            self.ok(self.ok_labels[0])
        else:
            self.focus_last()

    def validate(self, focus_next=False):
        choice = self.normalize(self.input.edit_text)
        choices = [choice] if choice else []

        for choice, checkbox in self.checkboxes.items():
            if (choice not in choices) and checkbox.get_state():
                choices.append(choice)

        import logging
        logging.debug('Choices: %s' % choices)
        if self.create:
            for choice in choices:
                if (choice not in self.choices) and not self.create(choice):
                    return False

        return self.focus_next() if focus_next else ','.join(choices)

    def none(self):
        self._emit('close')
        self.action(None, pressed=self.allow_none)

    def ok(self, ok):
        choice = self.validate()
        if choice or (choice is not False and self.allow_none):
            self._emit('close')
            self.action(choice or None, pressed=ok)
