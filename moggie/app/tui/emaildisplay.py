import logging
import re
import sys
import urwid

from ...email.metadata import Metadata
from ...email.addresses import AddressInfo
from ...api.requests import RequestCommand
from ...util.dumbcode import to_json

from .widgets import *


class EmailDisplay(urwid.ListBox):
    COLUMN_NEEDS = 60
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, ctx_src_id, metadata,
            username=None, password=None, parsed=None):
        self.tui_frame = tui_frame
        self.ctx_src_id = ctx_src_id
        self.metadata = metadata
        self.email = parsed
        self.uuid = self.metadata['uuid']
        self.crumb = self.metadata.get('subject', '(no subject)')

        self.rendered_width = self.COLUMN_NEEDS
        self.email_body = urwid.Text('(loading...)')
        self.widgets = urwid.SimpleListWalker(
            list(self.headers()) + [self.email_body])

        self.search_obj = self.get_search_obj(metadata, username, password)
        self.tui_frame.send_with_context(self.search_obj, self.ctx_src_id)

        urwid.ListBox.__init__(self, self.widgets)

        me = 'emaildisplay'
        _h = self.tui_frame.conn_manager.add_handler
        self.cm_handler_ids = [
            _h(me, ctx_src_id, 'cli:parse', self.incoming_parse)]

    def cleanup(self):
        self.tui_frame.conn_manager.del_handler(*self.cm_handler_ids)
        del self.tui_frame
        del self.metadata
        del self.widgets
        del self.email

    def headers(self):
        for field in ('Date:', 'To:', 'Cc:', 'From:', 'Reply-To:', 'Subject:'):
            fkey = field[:-1].lower()
            if fkey not in self.metadata:
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
                if not val:
                    continue
                yield urwid.Columns([
                    ('fixed',  8, urwid.Text(('email_key_'+fkey, field), align='right')),
                    ('weight', 4, urwid.Text(('email_val_'+fkey, val)))],
                    dividechars=1)
                field = ''
        yield(urwid.Divider())

    def render(self, size, focus=False):
        self.rendered_width = size[0]
        return super().render(size, focus=focus)

    def get_search_obj(self, metadata, username, password):
        parse_args = [
            # Reset to the bare minimum, we can as for more if the user
            # wants it (and as the app evolves).
            '--with-nothing=Y',
            '--with-headers=Y',
            '--with-structure=Y',
            '--with-text=Y',
            '--ignore-index=N',
            # We convert the HTML to text here, so we can wrap lines.
            '--with-html-text=N',
            '--with-html-clean=N',
            '--with-html=Y']
        if username:
            parse_args.append('--username=%s' % username)
        if password:
            parse_args.append('--password=%s' % password)
        parse_args.append(to_json(metadata))
        return RequestCommand('parse', args=parse_args)

    def incoming_parse(self, source, message):
        from moggie.security.html import html_to_markdown

        def _to_md(txt):
            return html_to_markdown(txt,
                no_images=True,
                wrap=min(self.COLUMN_WANTS, self.rendered_width-1))

        for data in message['data']:
            if isinstance(data, dict):
                self.email = data['parsed']

        email_txts = {'text/plain': '', 'text/html': ''}
        for ctype, fmt in (
                ('text/plain', lambda t: t),
                ('text/html',  _to_md)):
            for part in self.email['_PARTS']:
                if part['content-type'][0] == ctype:
                    email_txts[ctype] += fmt(part.get('_TEXT', ''))

        # This is a heuristic to avoid the case where silly people
        # send a plain-text part that says "there is no text part".
        len_html = len(email_txts['text/html'])
        len_text = len(email_txts['text/plain'])
        if len_html > 60:
            email_text = email_txts['text/html']
        else:
            email_text = email_txts['text/plain']

        email_text = re.sub(
            r'\n\s*\n', '\n\n', email_text.replace('\r', ''), flags=re.DOTALL)

        self.email_body = urwid.Text(email_text.strip())
        self.widgets[-1] = self.email_body
