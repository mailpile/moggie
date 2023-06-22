import logging
import re
import sys
import urwid

from ...email.addresses import AddressInfo
from ...email.metadata import Metadata
from ...email.parsemime import MessagePart
from ...api.requests import RequestCommand
from ...util.dumbcode import to_json

from .widgets import *
from .messagedialog import MessageDialog
from .decorations import EMOJI, ENVELOPES


class EmailDisplay(urwid.ListBox):
    COLUMN_NEEDS = 60
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    VIEW_EMAIL = 1
    VIEW_REPORT = 2
    VIEW_SOURCE = 3
    VIEWS = (VIEW_EMAIL, VIEW_REPORT, VIEW_SOURCE)
    VIEW_CRUMBS = {
        VIEW_EMAIL: 'E-mail',
        VIEW_REPORT: 'E-mail Analysis',
        VIEW_SOURCE: 'E-mail Source'}

    MESSAGE_MISSING = """\
Message is missing

Moggie is pretty sure this e-mail exists
because other e-mails referenced it.

However, Moggie has yet to receive a copy.
"""

    def __init__(self, tui_frame, ctx_src_id, metadata,
            username=None, password=None, parsed=None):
        self.tui_frame = tui_frame
        self.ctx_src_id = ctx_src_id
        self.metadata = metadata
        self.username = username
        self.password = password
        self.email = parsed
        self.view = self.VIEW_EMAIL
        self.has_attachments = None
        self.uuid = self.metadata['uuid']
        self.crumb = self.VIEW_CRUMBS[self.view]

        self.column_hks = [
            ('col_hk', 'r:'), 'Reply', ' ',
            ('col_hk', 'F:'), 'Forward', ' ',
            ('col_hk', 'V:'), 'Change View']

        self.rendered_width = self.COLUMN_NEEDS
        self.widgets = urwid.SimpleListWalker([])
        self.header_lines = list(self.headers())
        self.email_display = [self.no_body('Loading ...')]
        urwid.ListBox.__init__(self, self.widgets)
        self.update_content()

        self.set_focus(len(self.widgets)-1)

        self.search_obj = self.get_search_obj()
        self.send_email_request()

        me = 'emaildisplay'
        _h = self.tui_frame.conn_manager.add_handler
        self.cm_handler_ids = [
            _h(me, ctx_src_id, 'cli:show', self.incoming_parse),
            _h(me, ctx_src_id, 'cli:parse', self.incoming_parse)]

    def send_email_request(self):
        if self.metadata.get('missing'):
            self.email_display = [self.no_body(self.MESSAGE_MISSING)]
            self.update_content()

        self.tui_frame.send_with_context(self.search_obj, self.ctx_src_id)

    def cleanup(self):
        self.tui_frame.conn_manager.del_handler(*self.cm_handler_ids)
        del self.tui_frame
        del self.metadata
        del self.email

    def keypress(self, size, key):
        # FIXME: Should probably be using CommandMap !
        if key == 'F':
            self.on_forward()
            return None
        if key == 'r':
            self.on_reply()
            return None
        if key == 'R':
            self.on_reply(group=False)
        if key == 'V':
            self.toggle_view()
            return None
        return super().keypress(size, key)

    def update_content(self, update=False):
        self.widgets[:] = self.header_lines + self.email_display

    def describe_crypto_state(self, part, short=False):
        crypto = (part.get('_CRYPTO') or {}).get('summary', '')
        cstate = ''
        if 'verified' in crypto:
            cstate += EMOJI.get('verified', 'v ')
        else:
            cstate += '  '
        if 'decrypted' in crypto:
            cstate += EMOJI.get('encrypted', 'e ')
        else:
            cstate += '  '

        # FIXME: Describe better
        if not short:
            cstate = crypto.replace('+', ', ') + ' ' + cstate

        return cstate

    def headers(self):
        att_label = EMOJI.get('attachment', 'Attachment')
        fields = {
            'Date:': None,
            'To:': None,
            'Cc:': None,
            'From:': '(unknown sender)',
            'Reply-To:': None,
            'Subject:': '(no subject)'}
        fwidth = max(len(f) for f in [att_label] + list(fields.keys()))

        def _on_attachment(att, filename):
            return lambda e: self.on_attachment(att, filename)

        def line(fkey, field, value, cstate, action=None):
            fkey = fkey[:4]
            field = urwid.Text(('email_key_'+fkey, field), align='right')
            value = urwid.Text(('email_val_'+fkey, value))
            cstate = urwid.Text(('email_cs_'+fkey, cstate))
            if action is not None:
                value = Selectable(value, on_select={'enter': action})
            return urwid.Columns([
                    ('fixed', fwidth,  field),
                    ('weight',     4,  value),
                    ('fixed',      4, cstate),
                ], dividechars=1)

        for field, default in fields.items():
            cstate = '    '  # FIXME?
            fkey = field[:-1].lower()
            if fkey not in self.metadata:
                if default:
                    yield line(fkey, field, default, cstate)
                continue

            value = self.metadata[fkey]
            if not isinstance(value, list):
                value = [value]

            for val in value:
                if isinstance(val, dict):
                    val = AddressInfo(**val)
                if isinstance(val, AddressInfo):
                    if val.fn:
                        val = '%s <%s>' % (val.fn, val.address)
                    else:
                        val = '<%s>' % val.address
                else:
                    val = str(val).strip()
                if not val and not default:
                    continue
                yield line(fkey, field, val or default, cstate)
                field = ''

        if self.email:
            self.has_attachments = []
            for part in MessagePart.iter_parts(self.email):
                ctype = part.get('content-type', ['', {}])
                if (ctype[0] == 'application/pgp-signature'
                        and not self.email.get('_OPENPGP_ERRORS')):
                    continue

                # Get crypto summary, if we have one
                cstate = self.describe_crypto_state(part, short=True)

                filename = None
                disp = part.get('content-disposition', ['', {}])
                if disp[0] == 'attachment':
                    filename = ctype[1].get('name') or disp[1].get('filename')
                if filename:
                    yield line('att', att_label, filename, cstate,
                        action=_on_attachment(part, filename))
                    self.has_attachments.append(filename)

        yield(urwid.Divider())

    def render(self, size, focus=False):
        self.rendered_width = size[0]
        return super().render(size, focus=focus)

    def toggle_view(self):
        next_view = (self.VIEWS.index(self.view) + 1)  % len(self.VIEWS)
        self.view = self.VIEWS[next_view]
        self.search_obj = self.get_search_obj()
        self.send_email_request()

    def get_search_obj(self):
        if self.view == self.VIEW_SOURCE:
            command = 'show'
            parse_args = ['--part=0']

        elif self.view == self.VIEW_REPORT:
            command = 'parse'
            parse_args = [
                '--allow-network',
                '--with-everything=Y',
                '--format=text']

        else:
            command = 'parse'
            parse_args = [
                # Reset to the bare minimum, we can as for more if the user
                # wants it (and as the app evolves).
                '--with-nothing=Y',
                '--with-headers=Y',
                '--with-structure=Y',
                '--with-text=Y',
                '--with-openpgp=Y',
                '--ignore-index=N',
                # We convert the HTML to text here, so we can wrap lines.
                '--with-html-text=N',
                '--with-html-clean=N',
                '--with-html=Y']
        if self.username:
            parse_args.append('--username=%s' % self.username)
        if self.password:
            parse_args.append('--password=%s' % self.password)
        parse_args.append(to_json(self.metadata))
        return RequestCommand(command, args=parse_args)

    def empty_body(self):
        if self.metadata.get('missing'):
            return self.no_body(self.MESSAGE_MISSING)
        elif self.has_attachments:
            return self.no_body('This message only has attachments:\n\n'
                + '\n'.join(self.has_attachments) + '\n')
        else:
            return self.no_body('Empty message')

    def incoming_parse(self, source, message):
        logging.debug('msg=%.2048s' % message)
        if message['req_id'] != self.search_obj['req_id']:
            return

        self.email_display = []
        if message['mimetype'] == 'application/moggie-internal':
            for data in message['data']:
                if isinstance(data, dict):
                    self.email = data['parsed']
                    break
            if self.email:
                self.header_lines = list(self.headers())
                self.email_display = self.parsed_email_to_widget_list()

        elif message['mimetype'] in ('text/plain', 'message/rfc822'):
            if message['data'].strip():
                self.email_display = [urwid.Text(message['data'], wrap='any')]
            else:
                self.email_display = [self.empty_body()]

        if not self.email_display:
            self.email_display = [self.no_body(
                'Failed to load or parse message, sorry!')]

        self.crumb = self.VIEW_CRUMBS[self.view]
        self.tui_frame.update_topbar()
        self.update_content()

    def parsed_email_to_widget_list(self):
        from moggie.security.html import html_to_markdown
        def _to_md(txt):
            return html_to_markdown(txt,
                no_images=True,
                wrap=min(self.COLUMN_WANTS, self.rendered_width-1))

        # FIXME: If some parts are verified, but others not, or some parts
        #        encrypted but others not, we should do something clever
        #        with the colors to de-emphasize the parts which lack crypto.

        email_txts = {'text/plain': [], 'text/html': []}
        email_lens = {'text/plain': 0, 'text/html': 0}
        have_cstate = False
        for ctype, fmt in (
                ('text/plain', lambda t: t),
                ('text/html',  _to_md)):
            # FIXME: Make notes of the crypto-states of each part
            for part in MessagePart.iter_parts(self.email):
                if part['content-type'][0] == ctype:
                    text = fmt(part.get('_TEXT', ''))
                    cstate = self.describe_crypto_state(part).strip()
                    have_cstate = have_cstate or cstate
                    email_txts[ctype].append((cstate, text))
                    email_lens[ctype] += len(text)

        # This is a heuristic to avoid the case where silly people
        # send a plain-text part that says "there is no text part".
        if email_lens['text/html'] > 60:
            email_text = email_txts['text/html']
        else:
            email_text = email_txts['text/plain']

        dl = EMOJI.get('downleft', '') + ' '
        widgets = []
        last_cstate = ''
        for cstate, text in email_text:
            text = re.sub(
                r'\n\s*\n', '\n\n', text.replace('\r', ''), flags=re.DOTALL
                ).strip()
            if text:
                if cstate != last_cstate:
                    label = cstate
                    if have_cstate and not label:
                        label = 'unverified, unencrypted'
                    widgets.append(urwid.Text(
                        ('email_cstate', dl + label + '   '), align='right'))
                    last_cstate = cstate
                bg = '' if (cstate or not have_cstate) else '_bg'
                widgets.append(urwid.Text(
                    ('email_body' + bg, text + '\n\n')))

        if widgets:
            return widgets
        else:
            return [self.empty_body()]

    def no_body(self, message):
        rows = self.tui_frame.max_child_rows() - len(self.header_lines)
        return urwid.BoxAdapter(
            SplashCat(decoration=ENVELOPES, message=message),
            rows)

    def on_attachment(self, att, filename):
        logging.debug('FIXME: User selected part %s' % att)
        self.tui_frame.topbar.open_with(
            MessageDialog, 'FIXME: Do things with %s' % filename)

    def on_forward(self):
        logging.debug('FIXME: User wants to forward')
        self.tui_frame.topbar.open_with(
            MessageDialog, 'FIXME: Forwarding does not yet work!')

    def on_reply(self, group=True):
        logging.debug('FIXME: User wants to reply (group=%s)' % group)
        self.tui_frame.topbar.open_with(
            MessageDialog, 'FIXME: Repying does not yet work!')
