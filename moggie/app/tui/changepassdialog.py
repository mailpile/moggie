import urwid

from .widgets import *


class ChangePassDialog(urwid.WidgetWrap):
    HELP_TEXT_UNLOCKED = """\
Please enter a new passphrase (twice). This will rotate
all of the encryption keys and require the new
passphrase to unlock the app from now on.

If you check `Close active sessions`, this will also
disconnect any other connected apps and require they
re-connect and re-authenticate. This is recommended
if you suspect your privacy has been compromised.
"""

    HELP_TEXT = """\
Please enter your current (old) passphrase and provide
a new one. This will rotate all of the encryption keys
and require the new passphrase to unlock the app.

If you check `Close active sessions`, this will also
disconnect any other connected apps and require they
re-connect and re-authenticate. This is recommended
if you suspect your privacy has been compromised.
"""
    HELP_PASS = """\

The most secure (and memorable) passphrases consist of
multiple words, chosen at random from a dictionary.

A suggestion: %s


"""

    WANTED_WIDTH = 60
    WANTED_HEIGHT = 8 + max(len(HELP_TEXT.splitlines()),
                            len(HELP_PASS.splitlines()))

    signals = ['close']

    def _passphrases(self):
        if isinstance(self.old_pass, urwid.Edit):
            oldp = self.old_pass.edit_text.replace('\n', '')
        else:
            oldp = None
        newp1 = self.new_pass1.edit_text.replace('\n', '')
        newp2 = self.new_pass2.edit_text.replace('\n', '')
        return oldp, newp1, newp2

    def cleanup(self):
        if not self.doingit:
            self.doingit = True

            oldp, newp1, newp2 = self._passphrases()

            if isinstance(self.old_pass, urwid.Edit):
                self.old_pass.set_edit_text(oldp)
            self.new_pass1.set_edit_text(newp1)
            self.new_pass2.set_edit_text(newp2)

            self.doingit = False

    def doit(self, passphrase):
        oldp, newp1, newp2 = self._passphrases()

        if '\n' in passphrase:
            if self.pile.focus_position < 2:
                self.pile.focus_position += 1
                if self.pile.focus_position == 2:
                    if oldp:
                        pass  # FIXME: Validate old passphrase right away?
                    if newp1 == '':
                        self.update_pile(
                            'WARNING: A blank passphrase disables the app lock',
                            focus=2)
                    elif len(newp1) < 8:
                        # FIXME: Add better checks!
                        random_pp = 'Alpha Beta FIXME FIXME FIXME'
                        self.update_pile(
                            'WARNING: That passphrase is insecure',
                            help_text=self.HELP_PASS % random_pp,
                            focus=2)
                    else:
                        self.update_pile('', focus=2)

            elif newp1 == newp2:
                # self.tui.change(passphrase)
                self.update_pile(
                    'Updating passphrase and rotating keys...', focus=5)
                self.tui.change_passphrase(oldp, newp1,
                     disconnect=self.disconnect.get_state())
                emit_soon(self, 'close', seconds=2)

            elif newp1 != newp2:
                self.update_pile('New passphrases do not match!', focus=2)

        return passphrase.replace('\n', '')

    def update_pile(self, message='', focus=0, help_text=HELP_TEXT):
        if focus == 0 and not isinstance(self.old_pass, urwid.Edit):
            focus = 1
            if help_text == self.HELP_TEXT:
                help_text = self.HELP_TEXT_UNLOCKED

        widgets = [
            urwid.Columns([
                ('weight', 1, self.old_pass),
                ('fixed',  3, self.close_button)]),
            self.new_pass1,
            self.new_pass2,
            urwid.Divider(),
            self.disconnect,
            urwid.Text(('status', message), 'center'),
            urwid.Text(('popsubtle', help_text))]
        self.pile.contents = ([(w, ('pack', None)) for w in widgets])
        self.pile.focus_position = focus

    def __init__(self, tui):
        self.tui = tui
        self.doingit = False

        self.disconnect = urwid.CheckBox('Close active sessions.', False)

        self.close_button = CloseButton(
            lambda x: self._emit('close'), style='popsubtle')

        if self.tui.was_locked:
            self.old_pass = urwid.Edit('Old passphrase: ',
                multiline=True, mask='*', allow_tab=False, wrap='clip')
        else:
            self.old_pass = urwid.Text(
                'App does not currently require a passphrase.')

        self.new_pass1 = urwid.Edit('New passphrase: ',
            multiline=True, mask='*', allow_tab=False, wrap='clip')
        self.new_pass2 = urwid.Edit('    (repeated): ',
            multiline=True, mask='*', allow_tab=False, wrap='clip')

        for w in (self.old_pass, self.new_pass1, self.new_pass2):
            if not isinstance(w, urwid.Edit):
                continue
            urwid.connect_signal(w, 'change', lambda b,t: self.doit(t))
            urwid.connect_signal(w, 'postchange', lambda b,t: self.cleanup())

        self.pile = urwid.Pile([])
        self.update_pile()

        super().__init__(urwid.LineBox(
            urwid.AttrWrap(urwid.Filler(self.pile), 'popbg')))
