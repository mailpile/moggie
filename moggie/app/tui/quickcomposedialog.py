import logging
import urwid
from urwid_readline import ReadlineEdit

from ...email.addresses import AddressHeaderParser
from ...util.dumbcode import to_json
from .widgets import *


class EditBody(ReadlineEdit):
    signals = ['next'] + ReadlineEdit.signals

    def keypress(self, size, key):
        if key in ('tab', ):
            self._emit('next')
        return super().keypress(size, key)


class EditHeader(ReadlineEdit):
    signals = ['next'] + ReadlineEdit.signals

    def keypress(self, size, key):
        if key in ('enter', 'tab'):
            self._emit('next')
        return super().keypress(size, key)


class QuickComposeDialog(urwid.WidgetWrap):
    WANTED_HEIGHT = 6
    WANTED_WIDTH = 60

    LOADING = '(Loading ...)'

    signals = ['close']

    def __init__(self, tui, recipients, reply_to_metadata=None):
        self.mog_ctx = tui.active_mog_ctx()
        self.tui = tui
        self.reply_to_metadata = reply_to_metadata
        self.subject = EditHeader('Subject: ', multiline=False, allow_tab=False)
        self.to_field = EditHeader('To: ', multiline=False, allow_tab=False) 
        self.edit_body = EditBody(multiline=True, allow_tab=False)
        self.quote = urwid.CheckBox('Quote original message', True)
        self.headers = [urwid.Text(self.LOADING)]
        self.sender = None
        self.error = None
        self.options = []
        self.plan = {}

        def _right(widget, size):
            return urwid.Columns([
                ('weight', 1, urwid.Text('')),
                ('fixed', size, widget)])

        if reply_to_metadata:
            self.options.append(_right(self.quote, len(self.quote.label)+4))

        self.pile = urwid.Pile([])
        self.update_pile()

        plan_args = ['compose']
        if recipients:
            plan_args += ['--emailing=%s' % r for r in recipients]

        if reply_to_metadata:
            plan_args[0] = 'reply1' if recipients else 'reply'
            plan_args += ['--message=' + to_json(reply_to_metadata)]

        def _auto_focus_away(a, b):
            if self.edit_body.edit_text.endswith('\n\n\n'):
                self.edit_body.edit_text = self.edit_body.edit_text.rstrip()
                self.focus_buttons()
            else:
                self.clear_error()

        urwid.connect_signal(self.subject, 'next',
            lambda e: self.pile.set_focus(len(self.headers) + 1))
        urwid.connect_signal(self.edit_body, 'next',
            lambda e: self.pile.set_focus(len(self.headers) + 2))
        urwid.connect_signal(self.edit_body, 'postchange', _auto_focus_away)
        urwid.connect_signal(self.subject, 'postchange', self.clear_error)

        if reply_to_metadata:
            self.focus_editor()
        else:
            self.focus_subject()

        fill = urwid.Filler(self.pile)
        wrap = urwid.AttrWrap(fill, 'popbg')
        super().__init__(urwid.LineBox(wrap))

        # Fire off API request to update our data
        self.mog_ctx.plan(*plan_args, on_success=self.update_plan)

    def focus_subject(self):
        self.pile.set_focus(len(self.headers))

    def focus_editor(self):
        self.pile.set_focus(len(self.headers) + 1)

    def focus_buttons(self):
        self.pile.set_focus(len(self.headers) + 2)

    def clear_error(self, *args):
        if self.error:
            self.error = None
            self.update_pile()

    def update_plan(self, mog_ctx, plan_result):
        if not plan_result:
            return

        _id, self.plan = plan_result[0][0]

        self.sender = self.plan['send']['send-from'][0]
        self.subject.edit_text = self.plan['email'].get('subject', [''])[0]

        headers = []
        for hdr in ('To', 'Cc', 'Bcc'):
            rcpts = self.plan['email'].get(hdr.lower(), [])
            if rcpts:
                recipients = ', '.join(rcpts)
                if len(recipients) >= 54:
                    ahp = AddressHeaderParser(recipients)
                    cnt = ', +%d' % (len(ahp) - 1) if (len(ahp) > 1) else ''
                    recipients = ahp[0].friendly(max_width=54-len(cnt)) + cnt
                headers.append(urwid.Text('%s: %s' % (hdr, recipients)))

        self.headers[:] = headers
        self.update_pile()

    def make_buttons(self):
        if self.sender:
            sender = self.sender
            if len(sender) > 40:
                sender = sender[:38] + '..'
            sending = [
                ('fixed', 4+2, SimpleButton(
                    'Send',
                    lambda x: self.on_click_send(), style='popsubtle')),
                ('fixed', len(sender)+3+2, urwid.Text(
                    'as %s' % sender))]
        else:
            sending = [('fixed', len(self.LOADING)+2, urwid.Text(self.LOADING))]

        if self.error:
            error = [('fixed', len(self.error), urwid.Text(self.error))]
        else:
            error = None

        return (error or sending) + [
            ('weight', 1, urwid.Text(' ')),
            ('fixed', 6+2, SimpleButton(
                'Edit..',
                lambda x: self.on_click_full_editor(), style='popsubtle')),
            ('fixed', 6+2, SimpleButton(
                'Cancel',
                lambda b: self._emit('close'), style='popsubtle'))]

    def update_pile(self):
        self.pile.contents = [(w, ('pack', None)) for w in ([]
            + self.headers
            +[self.subject,
                urwid.LineBox(self.edit_body),
                urwid.Columns(self.make_buttons(), dividechars=1)]
            + self.options)]
        self.tui.redraw()
        return self.pile

    def wanted_height(self):
        return (self.WANTED_HEIGHT
            + len(self.headers)
            + len(self.options)
            + self.subject.rows((self.WANTED_WIDTH-4,))
            + self.edit_body.rows((self.WANTED_WIDTH-4,))
            - 1)

    def plan_recipients(self):
        for hdr in ('To', 'Cc', 'Bcc'):
            rcpts = self.plan['email'].get(hdr.lower(), [])
            for rcpt in rcpts:
                 yield hdr, rcpt

    def validated_subject_and_message(self, warn=True):
        subject = self.subject.edit_text.strip()
        message = self.edit_body.edit_text.strip()
        if warn and (not subject or not message):
            self.error = 'Need both a subject and a message!'
            self.update_pile()
            self.focus_subject()

        return subject, message

    def want_quote(self):
        return self.quote.get_state()

    def generate_email(self, callback,
            require_message=True,
            skip_args=[],
            extra_args=[]):

        subject, message = self.validated_subject_and_message(warn=False)
        if require_message and subject is None and message is None:
             return

        self.plan['email']['subject'] = [subject or '...']
        self.plan['email']['message'] = [message or '...']
        if self.want_quote() and self.reply_to_metadata:
            self.plan['email']['quote'] = [to_json(self.reply_to_metadata)]
        elif 'quote' in self.plan['email']:
            del self.plan['email']['quote']

        self.mog_ctx.email(
            *(make_plan_args(self.plan, 'email', skip=skip_args) + extra_args),
            on_success=callback,
            on_error=self.on_error)

        self.error = 'Generating e-mail...'
        self.update_pile()

    def on_click_full_editor(self):
        from moggie.email.draft import MessageDraft

        draft = MessageDraft.FromPlan(self.plan)
        draft.subject = self.subject.edit_text.strip()
        draft.message = self.edit_body.edit_text.strip()

        if not self.want_quote():
            # Not quoting, so short ciruit directly to the composer
            self.tui.show_composer(self.mog_ctx, draft, add=True)
            self._emit('close')
            return

        def edit_generated_email(mog_ctx, result):
            logging.debug('====== result =====\n%s' % result[0])
            for part in result[0]['parsed']['_PARTS']:
                ctype = part.get('content-type', [''])[0]
                if '_TEXT' in part and ctype == 'text/plain':
                    draft.message = part['_TEXT'].replace('\r', '')

            self._emit('close')
            self.tui.show_composer(self.mog_ctx, draft, add=True)

        def parse_generated_email(mog_ctx, result):
            email = result[0]['_RFC822']
            self.mog_ctx.parse(
                '--ignore-index=Y',
                '--with-nothing=Y',
                '--with-text=Y',
                '--stdin=%s' % email,
                on_success=edit_generated_email,
                on_error=self.on_error)
            self.error = 'Parsing generated message...'
            self.update_pile()

        self.generate_email(parse_generated_email,
            require_message=False,
            skip_args=['signature', 'quoting'],
            extra_args=['--quoting=html'])

    def on_error(self, mog_ctx, details):
        logging.info('Load e-mail failed: %s' % (
            details.get('error') or details.get('exc_args') or 'unknown error',))
        self.error('Failed! Check logs')

    def on_click_send(self):
        self.generate_email(self.copy_generated_email)

    def copy_generated_email(self, mog_ctx, result):
        email = result[0]['_RFC822']
        self.error = 'Saving %d bytes...' % len(email)
        self.update_pile()
        self.mog_ctx.copy(*(
                ['-', '--stdin=%s' % email] +
                make_plan_args(self.plan, 'copy')),
            on_success=self.send_copied_email,
            on_error=self.on_error)

    def send_copied_email(self, mog_ctx, result):
        self.error = 'Scheduling message for sending...'
        self.update_pile()
        self.mog_ctx.send(*(
                make_plan_args(self.plan, 'send') +
                ['id:%s' % result[0]['idx']]),
            on_success=self.on_sent,
            on_error=self.on_error)

    def on_sent(self, mog_ctx, result):
        logging.debug('Send result: %s' % result)
        self.error = 'Success!'
        self.update_pile()
        self._emit('close')
