import copy
import hashlib
import logging
import re
import sys
import time

import urwid
import urwid_readline

from ...email.headers import format_header
from ...email.draft import MessageDraft
from ...util.friendly import friendly_datetime

from .widgets import *
from .chooseaddressdialog import ChooseAddressDialog
from .decorations import EMOJI
from .emaildisplay import EmailDisplay
from .messagedialog import MessageDialog


# We keep a global in-memory cache of recent composer sessions;
# this means we can let the user seamlessly navigate away from
# the composer at any time to do something else without blocking
# them with confirmation dialogs and without concern about losing
# any work.
# Note: This is different (lighter weight) from saving drafts to
#       persistent storage and may be redundant once that works.
LAST_MESSAGE_DRAFTS = []


class Composer(EmailDisplay):
    VIEW_COMPOSER = 4
    VIEW_DEFAULT = VIEW_COMPOSER
    VIEWS = (VIEW_COMPOSER,)
    VIEW_CRUMBS = {VIEW_COMPOSER: 'Composer'}

    NAME_FMT = 'composer-%.4f'

    NO_DATE = '(set on send)'
    NO_RECIPIENT = '(...)'
    NO_SUBJECT = '(no subject)'
    NO_BODY = '(empty body)'

    def __init__(self, mog_ctx, tui,
            message_draft=None,
            username=None, password=None):

        self.editor_body = None
        self.advanced_options = False
        self.plans = {}
        self.plan_id = None

        if message_draft is None:
            if LAST_MESSAGE_DRAFTS:
                message_draft = LAST_MESSAGE_DRAFTS.pop(-1)
            else:
                message_draft = self._message_draft()

        if message_draft.message == self.NO_BODY:
            message_draft.message = ''

        self.message_draft = message_draft

        message_draft_parsed = message_draft.parsed()
        logging.debug('Parsed: %s' % (message_draft_parsed,))

        super().__init__(mog_ctx, tui,
            metadata=message_draft_parsed,
            username=username,
            password=password)

        self.request_plan(self.metadata)

    def cleanup(self):
        if self.message_draft:
            self.update_message_draft()
            LAST_MESSAGE_DRAFTS.append(self.message_draft)

    def _initial_content(self):
        return self._create_editor()

    def _column_hotkeys(self):
        return [
            ('col_hk', 'CTRL+P:'), 'Postpone', ' ',
            ('col_hk', 'CTRL+D:'), 'Details']

    def _editline(parent, placeholder='', emit_enter=False):
        class MyEditLine(urwid_readline.ReadlineEdit):
            signals = ['enter'] + urwid_readline.ReadlineEdit.signals

            def __init__(mel, *args, **kwargs):
                mel.on_change = kwargs.pop('on_change', None)
                mel.is_multiline = kwargs.get('multiline', False)
                super().__init__(*args, **kwargs)
                if mel.on_change:
                    urwid.connect_signal(mel, 'postchange', mel.on_change)

            def keypress(mel, size, key):
                if key == 'backspace':  # Avoid backspace bubbling
                    if mel.edit_pos == 0:
                        return None
                if key == 'enter':
                    if emit_enter:
                        mel._emit('enter')
                        return None
                    elif not mel.is_multiline or mel.edit_text.endswith('\n\n'):
                        mel.edit_text = mel.edit_text.rstrip('\n')
                        parent.focus_next()
                        return None

                elif mel.edit_text == '':
                    if len(key) != 1:
                        mel.edit_text = placeholder

                elif len(key) == 1 or key in ('backspace', 'space', 'delete'):
                    if mel.edit_text.startswith(placeholder):
                        mel.edit_text = mel.edit_text[len(placeholder):]

                return super().keypress(size, key)

        return MyEditLine

    def _header_fields(self):
        # now = int(time.time())
        # now -= (now % 300)
        # date = format_header('Date', now).split(':', 1)[1].strip(),

        _edit_subject = self._editline(self.NO_SUBJECT)
        if self.advanced_options:
            return {
#FIXME          'Date:': self.NO_DATE,
                'To:': self.NO_RECIPIENT,
                'Cc:': self.NO_RECIPIENT,
                'Bcc:': self.NO_RECIPIENT,
                'Reply-To:': self.NO_RECIPIENT,
                'Subject:': (self.NO_SUBJECT, lambda t,v: _edit_subject(
                     edit_text=v,
                     on_change=self._update_subject))}
        else:
            return {
                'To:': self.NO_RECIPIENT,
                'Cc:': None,
                'Bcc:': None,
                'Subject:': (self.NO_SUBJECT, lambda t,v: _edit_subject(
                     edit_text=v,
                     on_change=self._update_subject))}

    def _update_subject(self, widget, old_subject):
        new_subject = widget.edit_text
        if new_subject == self.NO_SUBJECT:
            new_subject = ''
        self.metadata['subject'] = new_subject
        logging.debug('Subject is now: %s' % new_subject)

    def _message_draft(self):
        _v = lambda v: v[0] if isinstance(v, tuple) else v
        return MessageDraft(
            more=dict(
                (k[:-1].lower(), _v(v))
                for k, v in self._header_fields().items()
                if v),
            no_subject=self.NO_SUBJECT)

    def mark_read(self):
        pass

    def headers(self, all_editable=True):
        return list(super().headers(all_editable=all_editable))[:-1]

    def iter_attachments(self):
        for fn in self.metadata.get('attach', []):
            yield {}, ['application/octet-stream'], fn, ''

    def on_click_attachment(self, att, filename):
        logging.debug('on_attachment(%s, %s)' % (att, filename))
        #self.tui.show_modal(SaveOrOpenDialog,
        #    'Save or Open Attachment', self, att, filename)

    def on_click_header(self, which, field, text, val):
        if self.advanced_options:
            others = {}
        else:
            others={
                    'to': {'cc': 'Cc', 'bcc': 'Bcc'},
                    'cc': {'to': 'To', 'bcc': 'Bcc'},
                    'bcc': {'to': 'To', 'Cc': 'Cc'}
                }.get(which)

        self.tui.show_modal(ChooseAddressDialog, self.mog_ctx,
            which, field, val,
            self.choose_email,
            editline=self._editline(
                placeholder=self.NO_RECIPIENT,
                emit_enter=True),
            others=others)

    def choose_email(self, which, new_addr, initial_addr, remove, add):
        if initial_addr and not add:
            try:
                i = self.metadata[which].index(initial_addr)
            except (ValueError, KeyError):
                i = -1
        else:
            i = -1

        address_list = self.metadata.get(which)
        if remove:
            if address_list:
                for a in (new_addr, initial_addr):
                    if a in address_list:
                        address_list.remove(a)
        elif isinstance(address_list, list):
            if 0 <= i < len(address_list):
                address_list[i] = new_addr
            else:
                address_list.append(new_addr)
        else:
            self.metadata[which] = [new_addr]

        self.refresh(self.metadata)
        self.focus_next()

    def _create_editor(self):
        if self.editor_body is None:
            self.editor_body = self._editline()(multiline=True)
            self.editor_body.set_edit_text(self.message_draft.message or '')

            self.editor_sender = SimpleButton('...',
                box='%s',
                on_select=self.on_click_choose_sender)

            self.editor_status = urwid.Text('...', #'Draft saved at 14:10',
                                            align='center')

            self.editor_sign = urwid.CheckBox('Sign')
            self.editor_encrypt = urwid.CheckBox('Encrypt')
            self.editor_markdown = urwid.CheckBox('Markdown')

        message_extras_line = [
            fixed_column(SimpleButton, 'Attach File'),
            ('weight', 1, self.editor_status)]

        if self.advanced_options or self.editor_markdown.get_state():
            message_extras_line.append(('fixed', 4+8, self.editor_markdown))

        if self.advanced_options or self.editor_sign.get_state():
            message_extras_line.append(('fixed', 4+4, self.editor_sign))

        if self.advanced_options or self.editor_encrypt.get_state():
            message_extras_line.append(('fixed', 4+7, self.editor_encrypt))

        message_sending_line = [
            fixed_column(SimpleButton, 'Send', on_select=self.on_click_send),
            fixed_column(urwid.Text, 'as'),
            ('weight', 1, self.editor_sender),
            fixed_column(SimpleButton, 'Postpone', on_select=self.on_click_postpone),
            fixed_column(SimpleButton, 'Discard', on_select=self.on_click_discard)]

        return [
            urwid.LineBox(self.editor_body, title='Message Text'),
            # FIXME: Add attachments here? Or in the header?
            urwid.Columns(message_extras_line, dividechars=1),
            urwid.Columns(message_sending_line, dividechars=1)]

    def focus_next(self):
        self.update_message_draft()
        current_pos = self.focus_position
        for i, w in enumerate(self.widgets):
            if ((i > current_pos)
                    and hasattr(w, 'selectable')
                    and w.selectable()):
                try:
                    self.set_focus(i)
                    return True
                except IndexError:
                    pass
        return False

    def keypress(self, size, key):
        if key == 'tab':
            if self.focus_next():
                return None

        if key == 'ctrl p':
            self.on_click_postpone()
            return None

        if key == 'ctrl d':
            self.advanced_options = (not self.advanced_options)
            self.email_display = self._create_editor()
            self.refresh(self.metadata)
            return None

        if key in ('Q', 'q'):
            # Intercept so users don't accidentally quit the app w/o saving
            if self.on_click_discard():
                return None

        # Not super() because we don't want to enable the EmailDisplay keys
        return urwid.ListBox.keypress(self, size, key)

    def send_email_request(self, use_cache=True):
        if self.metadata:
            return super().send_email_request(use_cache=use_cache)

    def get_search_command(self):
        return None, None, None

        # FIXME: Not reached, but we need something like this when loading
        #        a draft from a mailbox instead of internal generation.

        command = self.mog_ctx.parse
        args = [
            # Reset to the bare minimum, we can as for more if the user
            # wants it (and as the app evolves).
            '--with-nothing=Y',
            '--with-metadata=Y',
            '--with-missing=N',
            '--with-headers=Y',
            '--with-structure=Y',
            '--with-text=Y',
            '--with-data=Y',    # FIXME: implement get_data!
            '--with-openpgp=Y',
            '--ignore-index=N',
            # We convert the HTML to text here, so we can wrap lines.
            '--with-html-text=N',
            '--with-html-clean=N',
            '--with-html=Y']

        return None, command, args

    def update_message_draft(self):
        for hdr in ('to', 'cc', 'bcc', 'reply-to', 'subject', 'attach'):
            val = self.metadata.get(hdr)
            if val is not None:
                self.message_draft.more[hdr] = val
            elif hdr in self.message_draft.more:
                del self.message_draft.more[hdr]

        self.message_draft.message = self.editor_body.edit_text.strip()
        logging.debug('updated: %s' % (self.message_draft.more,))

        # FIXME: Keep track of selected plan/sender, other preferences.

    def request_plan(self, metadata):
        plan_args = ['compose']
        for hdr in ('to', 'cc', 'bcc'):
            plan_args += ['--emailing=%s' % r
                for r in (metadata.get(hdr) or [])]

        def _update_plan(mog_ctx, plan_result):
            self.plans = dict(plan_result[0])
            if not self.plan_id or (self.plan_id not in self.plans):
                self.plan_id = plan_result[0][0][0]

            logging.debug('active=%s plans=%s' % (self.plan_id, self.plans))
            self.editor_sender.set_text(self.plans[self.plan_id]['email']['from'])
            self.tui.redraw()

        self.mog_ctx.plan(*plan_args, on_success=_update_plan)

    def refresh(self, metadata, update=True):
        # FIXME: Get a new sending plan for the current set of senders and recipients
        self.update_message_draft()
        super().refresh(metadata, update=update)

    def on_click_choose_sender(self, *unused_args):
        logging.debug('click: choose sender')

    def on_click_send(self, *unused_args):
        def on_send(send_at=None):
            try:
                self.generate_and_save_draft(
                    on_done=self.send_copied_email,
                    send_at=send_at,
                    will_send=True)
                return True
            except ValueError as e:
                self.report_progress(str(e))
            return False

        self.update_message_draft()
        self.tui.show_modal(MessageDialog,
            title='Send E-mail',
            message="Send this message...",
            actions={
                # FIXME
                'In 2 minutes': lambda: on_send('+%d' % (60 * 2)),
                'Tomorrow': lambda: on_send('+%d' % (3600 * 24)),
                'Now': on_send,
                'Cancel': lambda: False})

    def send_copied_email(self, mog_ctx, result):
        if not (result and result[0].get('idx')):
            logging.debug('send_copied_email: result=%s' % (result,))
            self.report_progress(
                'Failed to copy/generate: missing ID of new message')
        else:
            idx = 'id:%s' % result[0]['idx']
            self.report_progress('Sending message %s' % idx)
            self.mog_ctx.send(*(
                    make_plan_args(self.sending_plan, 'send') + [idx]),
                on_success=self.on_sent,
                on_error=self.on_error)

    def on_sent(self, mog_ctx, result):
        self.message_draft = None
        self.report_progress('Sent!')
        self.tui.col_remove(self)

    def on_click_postpone(self, *unused_args):
        self.update_message_draft()
        self.generate_and_save_draft(self.on_postpone_saved)

    def on_postpone_saved(self, mog_ctx, result):
        self.tui.col_remove(self)

    def on_click_discard(self, *unused_args):
        def on_discard_done(*args):
            for result in (args[1] if args else []):
                if isinstance(result, dict) and 'history' in result:
                    self.tui.undoable.append((self.mog_ctx.tag, result['history']))
                    logging.debug('Undoable: %s' % (result['history'],))
            self.message_draft = None
            self.tui.col_remove(self)
            if args:
                self.tui.refresh_all()

        def on_confirm_discard(*args):
            idx = self.metadata.get('idx')
            if idx:
                self.mog_ctx.tag(*[
                        '-drafts', '+trash',
                        '--comment=Discarded draft',
                        '--', 'id:%s' % idx],
                    on_success=on_discard_done)
            else:
                self.on_discard_done()

        # How much effort has been put in?
        self.update_message_draft()
        msg_text = self.message_draft.subject +'\n'+ self.message_draft.message
        msg_lines = [line
            for line in msg_text.strip().splitlines()
            if line.strip() and not line.startswith('>')]
        char_count = sum(len(l) for l in msg_lines) if msg_lines else 0

        if (len(msg_lines) > 4) or (char_count > 40):
            self.tui.show_modal(MessageDialog,
                title='Discard draft?',
                message="Delete and discard this draft?",
                actions={
                    'Yes': on_confirm_discard,
                    'No': lambda: True})
            # For self.keypress(), let the handler know we intercepted.
            return True
        else:
            on_confirm_discard()
            return False

    def report_progress(self, message):
        self.editor_status.set_text(message)
        self.tui.redraw()

    def generate_and_save_draft(self,
            on_done=None,
            send_at=None,
            will_send=False):
        plan = copy.deepcopy(self.plans[self.plan_id])

        if will_send:
            self.sending_plan = plan
            if not self.message_draft.subject:
                raise ValueError('Please enter a subject')
            if not self.message_draft.message:
                raise ValueError('Please provide a message body')
        else:
            self.sending_plan = {}
            for skip in ('signature',):
                if skip in plan['email']:
                    del plan['email'][skip]

        if send_at is not None:
            plan['send']['send-at'] = [send_at]
        elif 'send-at' in plan['send']:
            del plan['send']['send-at']

        for var, val, dflt in (
                ('message-id', self.message_draft.message_id, None),
                ('subject', self.message_draft.subject, self.NO_SUBJECT),
                ('message', self.message_draft.message, self.NO_BODY)):
            if val:
                plan['email'][var] = [val]
            elif dflt:
                plan['email'][var] = [dflt]
            elif var in plan['email']:
                del plan['email'][var]

        for hdr in ('to', 'cc', 'bcc'):
            vals = self.message_draft.more.get(hdr) or []
            if vals:
                plan['email'][hdr] = vals
            elif hdr in plan['email']:
                del plan['email'][hdr]

        def _generated(mog_ctx, result):
            from .emaildisplay import clear_result_cache
            clear_result_cache()

            logging.debug('_generated() result: %s' % result)

            email = result[0]['_RFC822']
            self.message_draft.message_id = result[0]['message-id']

            # FIXME: Wrap on_done, record result[0]['idx']] so we can delete it

            self.report_progress('Saving %d bytes...' % len(email))
            self.mog_ctx.copy(*(
                    ['-', '--stdin=%s' % email] +
                    make_plan_args(plan, 'copy')),
                on_success=on_done,
                on_error=self.on_error)

        # FIXME: We probably need to embed the current plan in the e-mail
        #        headers themselves, for persistence when we load a draft
        #        from a message?

        self.mog_ctx.email(*make_plan_args(plan, 'email'),
            on_success=_generated,
            on_error=self.on_error)

    def on_error(self, mog_ctx, details):
        logging.info('Failed to generate/save/send e-mail: %s' % (
            details.get('error') or details.get('exc_args') or 'unknown error',))
        self.report_progress('Failed! Check logs for details')  #FIXME

