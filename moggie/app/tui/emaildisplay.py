import re
import sys
import urwid

from ...email.metadata import Metadata
from ...email.addresses import AddressInfo
from ...jmap.requests import RequestEmail

from .widgets import *


class EmailDisplay(urwid.ListBox):
    COLUMN_NEEDS = 60
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'

    def __init__(self, tui_frame, metadata, parsed=None):
        self.tui_frame = tui_frame
        self.metadata = Metadata(*metadata)
        self.parsed = self.metadata.parsed()
        self.email = parsed
        self.uuid = self.metadata.uuid_asc
        self.crumb = self.parsed.get('subject', 'FIXME')

        self.email_body = urwid.Text('(loading...)')
        self.widgets = urwid.SimpleListWalker(
            list(self.headers()) + [self.email_body])

        self.search_obj = RequestEmail(self.metadata, text=True)
        self.tui_frame.app_bridge.send_json(self.search_obj)

        urwid.ListBox.__init__(self, self.widgets)

    def headers(self):
        for field in ('Date:', 'To:', 'Cc:', 'From:', 'Reply-To:', 'Subject:'):
            fkey = field[:-1].lower()
            if fkey not in self.parsed:
                continue

            value = self.parsed[fkey]
            if not isinstance(value, list):
                value = [value]

            for val in value:
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

    def cleanup(self):
        del self.tui_frame
        del self.email

    def incoming_message(self, message):
        if (message.get('prototype') != self.search_obj['prototype'] or
                message.get('req_id') != self.search_obj['req_id']):
            return
        self.email = message['email']

        email_text = ''
        for ctype in ('text/plain', 'text/html'):
            for part in self.email['_PARTS']:
                if part['content-type'][0] == ctype:
                    email_text += part.get('_TEXT', '')
            if email_text:
                break
        email_text = re.sub(r'\n\s*\n', '\n\n', email_text, flags=re.DOTALL)

        self.email_body = urwid.Text(email_text)
        self.widgets[-1] = self.email_body
