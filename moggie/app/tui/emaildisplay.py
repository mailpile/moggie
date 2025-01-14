import copy
import hashlib
import logging
import re
import sys
import time
import urwid

from ...email.util import IDX_MAX
from ...email.addresses import AddressInfo, AddressHeaderParser
from ...email.metadata import Metadata
from ...email.parsemime import MessagePart
from ...util.dumbcode import to_json
from ...util.friendly import friendly_datetime, seconds_to_friendly_time
from ...util.mailpile import sha1b64

from ..cli.sendmail import SendingProgress

from .widgets import *
from .messagedialog import MessageDialog
from .decorations import EMOJI, ENVELOPES
from .saveoropendialog import SaveOrOpenDialog
from .headeractiondialog import HeaderActionDialog
from .openurldialog import OpenURLDialog


RESULT_CACHE = {}

def _h16(stuff):
    return hashlib.md5(bytes(stuff, 'utf-8')).hexdigest()[:12]


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

    PLAINTEXT_URL_RE = re.compile(
        r'((?:https?://|mailto:|www\.)[a-zA-Z0-9\._-]+[^\s)>]*)')
    MARKDOWN_URL_RE = re.compile(
        r'((?:[\*-] +|#+ +|\[\d+\] +)?\!?(?:\[.*?\]|))(\(\s*#\d+\.[a-f0-9]{12}\s*\)|\<#\d+\.[a-f0-9]{12}\>)([\.\? ]*)',
        re.DOTALL)

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
        self.send_progress = None
        self.username = username
        self.password = password
        self.selected = selected
        self.email = parsed
        self.view = self.VIEW_EMAIL
        self.marked_read = False
        self.retrying = False
        self.crumb = self.VIEW_CRUMBS[self.view]

        self.column_hks = [
            ('col_hk', 'r:'), 'Reply', ' ',
#           ('col_hk', 'F:'), 'Forward', ' ',
            ('col_hk', 'V:'), 'Change View']

        self.search_id = None
        self.rendered_width = self.COLUMN_NEEDS
        self.widgets = urwid.SimpleListWalker([])
        self.header_lines = []
        self.email_display = [self.no_body('Loading ...')]
        urwid.ListBox.__init__(self, self.widgets)

        self.refresh(metadata)

        adjust = 2
        if self.selected:
            adjust += 1
        if self.send_progress is not None:
            adjust += 1
        self.set_focus(len(self.header_lines) - adjust)

        # Expire things from our result cache
        deadline = time.time() - 600
        expired = [k for k, (t, c) in RESULT_CACHE.items() if t < deadline]
        for k in expired:
            del RESULT_CACHE[k]

        self.send_email_request()

    def refresh(self, metadata):
        self.metadata = metadata
        self.send_progress = sp = SendingProgress(metadata)
        if not sp.all_recipients:
            self.send_progress = None
        self.has_attachments = None
        self.uuid = self.metadata['uuid']
        self.header_lines = list(self.headers())
        self.update_content()

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
        def _on_click_header(which, field, text, val=None):
            return lambda e: self.on_click_header(which, field, text, val)
        def _on_click_cancel(sender, server, rcpt):
            return lambda e: self.on_click_cancel(sender, server, rcpt)
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
                    yield line(fkey, field, default, cstate)
                continue

            value = self.metadata[fkey]
            if not isinstance(value, list):
                value = [value]

            for val in value:
                txt = val
                if isinstance(txt, dict):
                    txt = AddressInfo(**val)
                if isinstance(txt, AddressInfo):
                    txt = txt.friendly(max_width=70)
                else:
                    txt = str(txt).strip()
                if not txt and not default:
                    continue
                yield line(fkey, field, txt or default, cstate,
                    action=_on_click_header(
                        fkey, field[:-1], txt or default,
                        val=val or None))
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

        if (self.send_progress is not None) and (self.view == self.VIEW_EMAIL):
            lines, progress, now = [], self.send_progress, int(time.time())
            explained = list(progress.explain())
            if explained:
                if self.retrying:
                    retry_now = urwid.Text(('send_retry_now', 'Working...'))
                    retry_now_w = len('Working...')  #FIXME
                elif progress.unsent:
                    retry_now = SimpleButton('Retry Now',
                        style='send_retry_now',
                        on_select=self.on_click_retry_now)
                    retry_now_w = 2 + len(retry_now.label)
                else:
                    retry_now = retry_now_w = None

                cols = [('weight',  1, urwid.Text(('send_plan_title', 'Sending Status:')))]
                if retry_now is not None:
                    cols.append(('fixed',  retry_now_w, retry_now))
                    lines.append(urwid.Columns(cols, dividechars=1))
                else:
                    lines.append(cols[0][-1])

                sender_w = max(len(ex[0]) for ex in explained)
                server_w = max(len(ex[1]) for ex in explained)
                rcpt_w = max(len(ex[2]) for ex in explained)
                for sender, server, rcpt, statcode, status, ts in explained:
                    sc = '_' + statcode

                    if progress.is_unsent(statcode):
                        cancel = SimpleButton('x',
                            on_select=_on_click_cancel(sender, server, rcpt),
                            style='send_cancel'+sc)
                        cancel = None  #FIXME
                        if ts > now:
                            status = '%s, next attempt in %s' % (
                                status, seconds_to_friendly_time(ts - now, parts=2))
                        else:
                            status = '%s, should send ASAP' % status
                    else:
                        status = '%s at %s' % (status, friendly_datetime(ts))
                        cancel = None

                    columns = [
                        ('fixed',        1, urwid.Text(' ')),
                        ('fixed', sender_w, urwid.Text(('send_sender'+sc, sender))),
                        ('fixed',        2, urwid.Text(('send_spacer'+sc, '->'))),
                        ('fixed',   rcpt_w, urwid.Text(('send_rcpt'+sc,   rcpt))),
                        ('fixed',        2, urwid.Text(('send_spacer'+sc, '::'))),
                        ('weight',       1, urwid.Text(('send_status'+sc, status)))]
                    if cancel:
                        columns.append(
                            ('fixed', 2+len(cancel.label), cancel))
                    lines.append(urwid.Columns(columns, dividechars=1))

            if self.send_progress.history:
                lines.append(urwid.Text(('send_hist_title', '\nHistory:')))

            for ts, info in sorted(self.send_progress.history)[-5:]:
                friendly_ts = friendly_datetime(ts)
                lines.append(urwid.Columns([
                    ('fixed',                1, urwid.Text(' ')),
                    ('fixed', len(friendly_ts), urwid.Text(('send_hist_date', friendly_ts))),
                    ('weight',               1, urwid.Text(('send_hist_info', info))),
                    ], dividechars=1))

            yield urwid.LineBox(urwid.Pile(lines))

        yield(urwid.Divider())

    def render(self, size, focus=False):
        self.rendered_width = size[0]
        return super().render(size, focus=focus)

    def toggle_view(self):
        next_view = (self.VIEWS.index(self.view) + 1)  % len(self.VIEWS)
        self.view = self.VIEWS[next_view]
        self.header_lines = list(self.headers())
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

    def send_email_request(self, use_cache=True):
        self.search_id, command, args = self.get_search_command()
        cached = RESULT_CACHE.get(self.search_id) if use_cache else None
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

        if isinstance(message, dict) and ('metadata' in message):
            self.refresh(message['metadata'])

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

        def _render_urls(txt, urls, wrap_at):
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

        def _to_md(txt):
            wrap_at = min(self.COLUMN_WANTS, self.rendered_width-1)
            txt, urls = html_to_markdown(txt,
                extract_urls=True,
                no_images=True,
                wrap=wrap_at)
            return _render_urls(txt, urls, wrap_at)

        def _linkify_urls(txt):
            wrap_at = min(self.COLUMN_WANTS, self.rendered_width-1)
            urls = {}
            def _u(m):
                url = m.group(0)
                if url.startswith('www.'):
                    url = 'https://' + url
                url_id = '#%d.%s' % (len(urls), _h16(url))
                urls[url_id] = url
                return '<%s>' % url_id
            txt = self.PLAINTEXT_URL_RE.sub(_u, _compact(txt))
            return _render_urls(txt, urls, wrap_at)

        email_txts = {'text/plain': [], 'text/html': []}
        email_lens = {'text/plain': 0, 'text/html': 0}
        have_cstate = False
        for ctype, fmt in (
                ('text/plain', _linkify_urls),
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

    def on_click_header(self, fkey, field, text, val=None):
        self.tui.topbar.open_with(HeaderActionDialog,
            text, None, self, self.metadata, fkey, val or self.metadata[fkey])

    def get_data(self, att, callback=None):
        logging.debug('FIXME: Should fetch attachment: %s' % att)

    def on_forward(self):
        logging.debug('FIXME: User wants to forward')
        self.tui.topbar.open_with(
            MessageDialog, 'FIXME: Forwarding does not yet work!')

    def on_reply(self):
        self.tui.topbar.open_with(HeaderActionDialog,
            self.metadata['subject'],
            None,
            self,
            self.metadata,
            'from',
            self.metadata['from'])

    def on_click_cancel(self, sender, server, rcpt):
        logging.debug('FIXME: Cancel %s[%s]=>%s' % (sender, server, rcpt))

    def on_click_retry_now(self, *args):
        self.retrying = True
        self.header_lines = list(self.headers())
        self.update_content()
        self.mog_ctx.send(
            *['--retry-now', 'id:%s' % self.metadata['idx']],
            on_success=self.on_retried)

    def on_retried(self, *args):
        self.retrying = False
        self.send_email_request(use_cache=False)
