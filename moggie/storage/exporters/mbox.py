from .base import *

from ..formats.mbox import FormatMbox
from ...email.util import quick_msgparse


class MboxExporter(BaseExporter):
    """
    Export messages as an mbox, updating/adding Status and Tags headers to
    match our internal metadata/tags.

    The format flavor is mboxcl (see https://en.wikipedia.org/wiki/Mbox),
    for maximum interoperability/reliability.
    """
    def calculate_idx(self, beg, transformed):
        hend, hdrs = quick_msgparse(transformed, 0)
        return int(FormatMbox.RangeToKey(beg, _data=hdrs)[1:], 16)

    def transform(self, metadata, message):
        return self.MboxTransform(metadata, message,
            add_moggie_sync=self.sync_id)

    @classmethod
    def MboxTransform(cls, metadata, message,
            mangle_from=True, add_from=True, add_moggie_sync=False,
            fix_newlines=True):
        return BaseExporter.Transform(metadata, message,
            add_from=add_from,
            add_headers=True,
            add_moggie_sync=add_moggie_sync,
            mangle_from=mangle_from,
            fix_newlines=fix_newlines)


if __name__ == '__main__':
    import sys, time
    from ...email.metadata import Metadata

    now = int(time.time())
    md = Metadata.ghost(msgid='<testing@moggie>')
    md[md.OFS_TIMESTAMP] = now
    md.more['tags'] = ['inbox', 'read']

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
    assert(b'Status: RO' in exported)
    assert(b'Tags: inbox, read' in exported)
    assert(b'>From Iceland with Love' in exported)
    assert(b'>>From Iceland with more Love' in exported)

    print('Tests passed OK')
