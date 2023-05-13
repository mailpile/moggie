import datetime
import re

from .base import *


class MboxExporter(BaseExporter):
    """
    Export messages as an mbox, updating/adding Status and Tags headers to
    match our internal metadata/tags.

    The format flavor is mboxcl (see https://en.wikipedia.org/wiki/Mbox),
    for maximum interoperability/reliability.
    """
    STATUS_HEADER = b'Status:'
    TAGS_HEADER = b'Tags:'
    CL_HEADER = b'Content-Length:'

    def transform(self, metadata, message):
        return self.MboxTransform(metadata, message)

    @classmethod
    def MboxTransform(cls, metadata, message,
            mangle_from=True, add_from=True, fix_newlines=True):
        # Convert to bytes and escape any bare From or >From lines
        if mangle_from:
            message = re.sub(b'\n(>*)From ', b'\n>\\1From ', message)

        if fix_newlines:
            # mbox is a Unix format, so this is the newline convention we want.
            eol = b'\n'
            message = bytearray(message.replace(b'\r\n', eol))
        else:
            # What is our newline convention?
            eol = b'\r\n' if (b'\r\n' in message[:256]) else b'\n'
            message = bytearray(message)

        # Add the leading From delimeter, if not already present
        if not message.startswith(b'From ') and add_from:
            dt = datetime.datetime.fromtimestamp(metadata.timestamp)
            dt = bytes(dt.strftime('%a %b %d %T %Y'), 'utf-8')
            message[:0] = bytearray(b'From moggie@localhost  %s%s' % (dt, eol))

        # Make sure we end with some newlines
        while not message.endswith(eol * 2):
            message += eol

        hend = message.find(eol * 2)
        def _add_or_update(header, new_value):
            h_beg = message[:hend].find(eol + header)
            if h_beg > 0:
                h_end = h_beg + 2 + message[h_beg+2:].find(eol)
            else:
                h_beg = h_end = hend
            message[h_beg:h_end] = eol + header + b' ' + new_value

        # Update message headers with tags
        if hend > 0:
            cl = len(message) - (hend + 3*len(eol))
            _add_or_update(cls.CL_HEADER, b'%d' % cl)

            tags = metadata.more.get('tags', [])
            if tags:
                taglist = bytes(', '.join(tags), 'utf-8')
                _add_or_update(cls.TAGS_HEADER, taglist)
            if 'unread' in tags:
                _add_or_update(cls.STATUS_HEADER, b'O')
            else:
                _add_or_update(cls.STATUS_HEADER, b'RO')

        return message


if __name__ == '__main__':
    import sys, time
    from ...email.metadata import Metadata

    now = int(time.time())
    md = Metadata.ghost(msgid='<testing@moggie>')
    md[md.OFS_TIMESTAMP] = now
    md.more['tags'] = ['inbox', 'unread']

    bio = ClosableBytesIO()
    with MboxExporter(bio) as exp:
        for i in range(0, 4):
            exp.export(md, b"""\
From: bre@example.org
To: bre@example.org
Date: Thu, 1 Sep 2022 03:37:29 +0200 (CEST)
Message-ID: <testing@moggie>
Status: N
Subject: ohai

This is very nice.
From Iceland with Love!
>From Iceland with more Love!
Why does mutt not unescape?""")

    exported = bio.dump()
    sys.stdout.buffer.write(exported)

    assert(b'From: bre@example.org' in exported)
    assert(b'Status: N' not in exported)
    assert(b'Status: O' in exported)
    assert(b'Tags: inbox, unread' in exported)
    assert(b'>From Iceland with Love' in exported)
    assert(b'>>From Iceland with more Love' in exported)

    print('Tests passed OK')
