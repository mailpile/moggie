import copy
import logging
import re
import sys
import time
import urwid

from ...email.util import IDX_MAX
from ...email.addresses import AddressInfo
from ...email.metadata import Metadata
from ...email.parsemime import MessagePart
from ...util.dumbcode import to_json
from ...util.mailpile import sha1b64

from .widgets import *
from .messagedialog import MessageDialog
from .decorations import EMOJI, ENVELOPES
from .saveoropendialog import SaveOrOpenDialog
from .openurldialog import OpenURLDialog


RESULT_CACHE = {}


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

    MIMETYPE_FILENAMES = {
        'message/rfc822': ('message', 'eml'),
        'image/jpeg': ('attachment', 'jpg')}

    MESSAGE_MISSING = """\
Message is missing

Moggie is pretty sure this e-mail exists
because other e-mails referenced it.

However, Moggie has yet to receive a copy.
"""

    MESSAGE_GONE = """\
Message unavailable

This may be a temporary network problem, or the
message may have been moved or deleted.

Technical details:
%s
"""


    def __init__(self, mog_ctx, tui, metadata,
            username=None, password=None, selected=False, parsed=None,
            mailbox=None):
        self.name = 'emaildisplay-%.4f' % time.time()
        self.mog_ctx = mog_ctx
        self.tui = tui
        self.mailbox = mailbox
        self.metadata = metadata
        self.username = username
        self.password = password
        self.selected = selected
        self.email = parsed
        self.view = self.VIEW_EMAIL
        self.marked_read = False
        self.has_attachments = None
        self.uuid = self.metadata['uuid']
        self.crumb = self.VIEW_CRUMBS[self.view]

        self.column_hks = [
#           ('col_hk', 'r:'), 'Reply', ' ',
#           ('col_hk', 'F:'), 'Forward', ' ',
            ('col_hk', 'V:'), 'Change View']

        self.search_id = None
        self.rendered_width = self.COLUMN_NEEDS
        self.widgets = urwid.SimpleListWalker([])
        self.header_lines = list(self.headers())
        self.email_display = [self.no_body('Loading ...')]
        urwid.ListBox.__init__(self, self.widgets)
        self.update_content()

        adjust = 3 if selected else 2
        self.set_focus(len(self.header_lines) - adjust)

        # Expire things from our result cache
        deadline = time.time() - 600
        expired = [k for k, (t, c) in RESULT_CACHE.items() if t < deadline]
        for k in expired:
            del RESULT_CACHE[k]

        self.send_email_request()

    def cleanup(self):
        self.mog_ctx.moggie.unsubscribe(self.name)

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
        def _on_header_select(which):
            return lambda e: True
        def _on_deselect():
            return lambda e: True

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
                    yield line(fkey, field, default, cstate,
                        action=_on_header_select(fkey))
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
                yield line(fkey, field, val or default, cstate,
                    action=_on_header_select(fkey))
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

                disp = part.get('content-disposition', ['', {}])
                filename = ctype[1].get('name') or disp[1].get('filename')

                if (not filename) and ctype[0] in self.MIMETYPE_FILENAMES:
                    fn, ext = self.MIMETYPE_FILENAMES[ctype[0]]
                    filename = '%s.%s' % (
                        part.get('content-description', [fn])[0],
                        ext)

                if filename:
                    yield line('att', att_label, filename, cstate,
                        action=_on_attachment(part, filename))
                    self.has_attachments.append(filename)

        if self.selected:
            yield line('sel',
                EMOJI.get('selected', 'x'),
                'Message selected (click to deselect)',
                '    ', action=_on_deselect())

        yield(urwid.Divider())

    def render(self, size, focus=False):
        self.rendered_width = size[0]
        return super().render(size, focus=focus)

    def toggle_view(self):
        next_view = (self.VIEWS.index(self.view) + 1)  % len(self.VIEWS)
        self.view = self.VIEWS[next_view]
        self.send_email_request()

    def get_search_command(self):
        if self.view == self.VIEW_SOURCE:
            command = self.mog_ctx.show
            args = ['--part=0']

        elif self.view == self.VIEW_REPORT:
            command = self.mog_ctx.parse
            args = [
                '--with-everything=Y',
                '--with-missing=Y',
                '--format=text']

        else:
            command = self.mog_ctx.parse
            args = [
                # Reset to the bare minimum, we can as for more if the user
                # wants it (and as the app evolves).
                '--with-nothing=Y',
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
        if self.username:
            args.append('--username=%s' % self.username)
        if self.password:
            args.append('--password=%s' % self.password)

        # This should be using the same logic as the emaillist tag search
        # term generation
        if self.metadata.get('idx'):
            args.append('id:%d' % self.metadata['idx'])
            if self.mailbox and (self.metadata['idx'] >= IDX_MAX):
                args.append('mailbox:%s' % self.mailbox)
        else:
            # FIXME: Sending the full metadata is silly. Also this is the
            #        parsed metadata, now the raw stuff.
            args.append(to_json(self.metadata))

        cache_id = sha1b64('%s' % args)
        return cache_id, command, args

    def send_email_request(self):
        self.search_id, command, args = self.get_search_command()
        cached = RESULT_CACHE.get(self.search_id)
        if cached:
            self.incoming_parse(None, copy.deepcopy(cached[1]), cached=True)
        else:
            if self.metadata.get('missing'):
                self.email_display = [self.no_body(self.MESSAGE_MISSING)]
                self.update_content()
            command(*args,
                on_success=self.incoming_parse,
                on_error=self.incoming_failed)

    def empty_body(self):
        if self.metadata.get('missing'):
            return self.no_body(self.MESSAGE_MISSING)
        elif self.has_attachments:
            return self.no_body('This message only has attachments:\n\n'
                + '\n'.join(self.has_attachments) + '\n')
        else:
            return self.no_body('Empty message')

    def mark_read(self):
        if self.marked_read or self.metadata.get('missing'):
            return

        # FIXME: If reading a mailbox directly, should we update it?
        #        If reading indirectly, should we update that?

        # FIXME: If we had the search context, we could use this as a signal
        #        to import the message into the index. Do we want that?
        # Tagging as read would do that.

        idx = self.metadata['idx'] if self.metadata else None
        if idx and (idx < IDX_MAX):
            if 'in:read' not in self.metadata.get('tags', []):
                self.mog_ctx.tag('+read', '--', 'id:%s' % idx)

    def incoming_failed(self, mog_ctx, details):
        logging.info('Load e-mail failed: %s' % (
            details.get('error') or details.get('exc_args') or 'unknown error',))
        if self.metadata.get('missing'):
            self.email_display = [self.no_body(self.MESSAGE_MISSING)]
        else:
            error = details.get('error') or '(unknown error)'
            self.email_display = [self.no_body(self.MESSAGE_GONE % error)]
            # FIXME: Give the user the option to reindex mailboxes, which
            #        should then update the metadata if successful.
        self.update_content()

    def incoming_parse(self, mog_ctx, message, cached=False):
        if message:
            if isinstance(message, list):
                message = message[0]
            message = try_get(message, 'data', message)

        RESULT_CACHE[self.search_id] = (time.time(), copy.deepcopy(message))
        self.email_display = []

        if isinstance(message, dict) and ('parsed' in message):
            self.email = message['parsed']
            if self.email:
                self.header_lines = list(self.headers())
                self.email_display = self.parsed_email_to_widget_list()

        elif isinstance(message, (str, bytes)):
            message = message.strip()
            if message:
                self.email_display = [urwid.Text(message, wrap='any')]
            else:
                self.email_display = [self.empty_body()]

        if not self.email_display:
            self.email_display = [self.no_body(
                'Failed to load or parse message, sorry!')]
            logging.debug('WAT: %s' % message)

        self.mark_read()
        self.crumb = self.VIEW_CRUMBS[self.view]
        self.tui.update_topbar()
        self.update_content()

    MARKDOWN_URL_RE = re.compile(
        r'((?:[\*-] +|#+ +)?\!?\[.*?\])(\(\s*#\d+\.[a-f0-9]+\s*\))([\.\? ]*)',
        re.DOTALL)

    def parsed_email_to_widget_list(self):
        from moggie.security.html import html_to_markdown

        def _compact(text):
            t = re.sub(
                r'\n\s*\n', '\n\n', text.replace('\r', ''), flags=re.DOTALL
                ).strip()
            return (t + '\n\n') if t else t

        def _on_att(att, filename):
            return lambda e: self.on_attachment(att, filename)

        def _on_click_url(url):
            return lambda e: self.on_click_url(url)

        def _to_md(txt):
            wrap_at = min(self.COLUMN_WANTS, self.rendered_width-1)
            txt, urls = html_to_markdown(txt,
                extract_urls=True,
                no_images=True,
                wrap=wrap_at)

            # We do a lot of fiddling with the whitespace here to get things
            # to look nice - this would get much cleaner if we didn't need
            # to always dedicate an entire line to each URL. Need to learn
            # to urwid better!

            text_cb_pairs = []
            cgroups = self.MARKDOWN_URL_RE.split(_compact(txt))
            while len(cgroups) >= 3:
                preamble = cgroups.pop(0)
                if not preamble.endswith('\n\n'):
                    preamble = preamble.rstrip()
                while preamble[:2] == '\n\n':
                    preamble = preamble[1:]
                text_cb_pairs.append((preamble, None))

                t1, t2, t3 = cgroups.pop(0), cgroups.pop(0), cgroups.pop(0)

                url_id = t2[1:-1].strip()
                url = urls.get(url_id)
                if url:
                    logging.debug('Have URL: %s=%s' % (url_id, url))
                    post = t3.lstrip()
                    text = t1.lstrip().replace('\n', ' ')

                    vurl = url[:wrap_at - len(text) - len(post) - 6]
                    text_cb_pairs.append((
                        '%s( %s%s )%s' % (
                            text,
                            vurl,
                            '..' if vurl != url else '',
                            post),
                        _on_click_url(url)))
                else:
                    logging.debug('Missing URL: %s' % url_id)
                    text_cb_pairs.append((text + target, None))
            while cgroups:
                text_cb_pairs.append((cgroups.pop(0), None))

            return text_cb_pairs

        email_txts = {'text/plain': [], 'text/html': []}
        email_lens = {'text/plain': 0, 'text/html': 0}
        have_cstate = False
        for ctype, fmt in (
                ('text/plain', lambda t: [(_compact(t), None)]),
                ('text/html',  _to_md)):
            for part in MessagePart.iter_parts(self.email):
                try:
                    filename = (
                        part['content-type'][1].get('name') or
                        part['content-disposition'][1].get('filename'))
                except (KeyError, IndexError):
                    filename = None

                if part['content-type'][0] == ctype:
                    text_cb_pairs = fmt(part.get('_TEXT', ''))
                    cstate = self.describe_crypto_state(part).strip()
                    have_cstate = have_cstate or cstate
                    for text, callback in text_cb_pairs:
                        email_txts[ctype].append((cstate, text, callback))
                        email_lens[ctype] += len(text)

                elif filename:
                    cstate = self.describe_crypto_state(part).strip()
                    email_txts[ctype].append((
                        cstate,
                        ' %s %s' % (
                            EMOJI.get('attachment', '- Attachment:'), filename),
                        _on_att(part, filename)))

        # This is a heuristic to avoid the case where silly people
        # send a plain-text part that says "there is no text part".
        if email_lens['text/html'] > 60:
            email_text = email_txts['text/html']
        else:
            email_text = email_txts['text/plain']

        dl = EMOJI.get('downleft', '') + ' '
        widgets = []
        last_cstate = ''
        for cstate, text, callback in email_text:
            if text:
                if cstate != last_cstate:
                    label = cstate
                    if have_cstate and not label:
                        label = 'unverified, unencrypted'
                    widgets.append(urwid.Text(
                        ('email_cstate', dl + label + '   '), align='right'))
                    last_cstate = cstate
                bg = '' if (cstate or not have_cstate) else '_bg'
                if callback:
                    widgets.append(Selectable(
                        urwid.Text(('email_body' + bg, text)),
                        on_select={'enter': callback}))
                else:
                    widgets.append(urwid.Text(('email_body' + bg, text)))

        if widgets:
            return widgets
        else:
            return [self.empty_body()]

    def no_body(self, message):
        rows = self.tui.max_child_rows() - len(self.header_lines)
        return urwid.BoxAdapter(
            SplashCat(decoration=ENVELOPES, message=message),
            rows)

    def on_click_url(self, url):
        self.tui.topbar.open_with(OpenURLDialog, 'Open URL', self, url)

    def on_attachment(self, att, filename):
        self.tui.topbar.open_with(SaveOrOpenDialog,
            'Save or Open Attachment', self, att, filename)

    def get_data(self, att, callback=None):
        logging.debug('FIXME: Should fetch attachment: %s' % att)

    def on_forward(self):
        logging.debug('FIXME: User wants to forward')
        self.tui.topbar.open_with(
            MessageDialog, 'FIXME: Forwarding does not yet work!')

    def on_reply(self, group=True):
        logging.debug('FIXME: User wants to reply (group=%s)' % group)
        self.tui.topbar.open_with(
            MessageDialog, 'FIXME: Repying does not yet work!')
