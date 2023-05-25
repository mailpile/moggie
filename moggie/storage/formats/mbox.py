import copy
import email.utils
import time
import os
import re

# FIXME: We really should use the MboxExporter.MboxTransform to escape
#        From lines and preserve other useful metadata, before writing to
#        the mbox.
#
#from ..exporters.mbox import MboxExporter
from ...email.metadata import Metadata
from ...email.headers import parse_header
from ...email.parsemime import parse_message as ep_parse_message
from ...email.util import quick_msgparse, make_ts_and_Metadata
from ...util.dumbcode import *

from . import tag_path
from .base import FormatBytes


class FormatMbox(FormatBytes):
    NAME = 'mbox'
    TAG = b'mbx'

    DELETED_MARKER = b"""\
From DELETED\r\n\
From: nobody <deleted@example.org>\r\n\
\r\n\
(deleted)\r\n"""
    DELETED_FILLER = b"                                                    \r\n"

    @classmethod
    def Magic(cls, parent, key, is_dir=None):
        try:
            if is_dir:
                return False
            return (parent[key][:5] == b'From ')
        except (KeyError, OSError):
            return False

    def __contains__(self, key):
        try:
            b,e = self._key_to_range(key)
            return (self.container[b:b+5] == b'From ')
        except (IndexError, ValueError):
            return False

    def __getitem__(self, key):
        b,e = self._key_to_range(key)
        if not (self.container[b:b+5] == b'From '):
            raise KeyError('Message not found')
        return self.container[b:e]

    def __delitem__(self, key):
        b,e = self._key_to_range(key)
        if not (self.container[b:b+5] == b'From '):
            # FIXME: We could do with a bit more checking here...
            raise KeyError('Message not found')

        length = e-b
        fill = (
            self.DELETED_MARKER +
            self.DELETED_FILLER * (1 + length // len(self.DELETED_FILLER))
            )[:length-2] + b'\r\n'
        self.container[b:e] = fill
        if self.parent:
            self.parent.need_compacting(tag_path(*self.path))

    def append(self, data):
        if isinstance(data, str):
            data = bytes(data, 'utf-8')
        if data[:5] != b'From ':
            raise ValueError('That does not look like an e-mail')
        return super().append(data)

    def __setitem__(self, key, value):
        b,e = self._key_to_range(key)
        if isinstance(value, str):
            value = bytes(value, 'utf-8')
        if value[:5] != b'From ':
            raise ValueError('That does not look like an e-mail')
        return super().__setitem__(key, value)

    def iter_email_offsets(self, skip=0, deleted=False):
        obj = self.container
        beg = 0
        end = 0
        delmark = self.DELETED_MARKER
        needs_compacting = 0
        try:
            while end < len(obj):
                hend, hdrs = quick_msgparse(obj, beg)

                end = obj.find(b'\nFrom ', hend-1)
                if end < 0:
                    end = len(obj)-1

                if (not deleted) and obj[beg:beg+len(delmark)] == delmark:
                    needs_compacting += 1
                elif skip > 0:
                    skip -= 1
                else:
                    yield beg, hend, end+1, hdrs

                beg = end+1
        except (ValueError, TypeError):
            return
        finally:
            if needs_compacting and self.parent:
                self.parent.need_compacting(tag_path(*self.path))

    def keys(self, skip=0):
        return (self._range_to_key(b, e)
            for b, he, e, hdrs in self.iter_email_offsets(skip=skip))

    def iter_email_metadata(self, skip=0, iterator=None):
        obj = self.container
        now = int(time.time())
        lts = 0
        try:
            if iterator is None:
                iterator = self.iter_email_offsets(skip=skip)
            for beg, hend, end, hdrs in iterator:
                path = self.get_tagged_path(self._range_to_key(beg, end))
                lts, md = make_ts_and_Metadata(
                    now, lts, obj[beg:hend], 
                    Metadata.PTR(Metadata.PTR.IS_FS, path, end-beg),
                    hdrs)
                yield(md)
        except (ValueError, TypeError):
            return

    # Thoughts:
    #   - We may want to write back metadata to the mailbox, to auto-export
    #     some of our tags (in particular read/unread status etc.)
    #   - If this happens during compaction, then this algorithm is broken
    #     since a message might get BIGGER.
    #   - If we crash during compaction, we may leave things in a corrupt
    #     state. Should we write out extra deletion markers to reduce the
    #     odds of that happening?
    #   - Rewriting in-place like this could be avoided if we rewrite the
    #     entire file, but that means we cannot delete if the disk is full
    #     and will often result in lots of extra I/O.
    #
    def iter_compact(self):
        obj = self.container
        nbeg = 0
        # FIXME: Could we be more smart somehow so not all the messages
        #        get moved around? Seems like a lot of complexity for
        #        only limited gain. Do the simple & correct thing for now.
        for beg, hend, end, hdrs in self.iter_email_offsets():
            hl = hend-beg
            if beg == nbeg:
                nend = end    # Message not moving, short circuit
            else:
                nb = nbeg
                for cbeg in range(beg, end, self.CHUNK_BYTES):
                    cend = min(end, cbeg + self.CHUNK_BYTES)
                    data = copy.copy(obj[cbeg:cend])
                    nend = nb+len(data)
                    obj[nb:nend] = data
                    # FIXME: Append deletion marker, if there is room?
                    # FIXME: Write deletion marker to old message if we
                    #        do not overlap?
                    nb = nend
            yield (beg, hend, end), (nbeg, nbeg+hl, nend), hdrs
            nbeg = nend
        obj.resize(nbeg)

    def iter_compact_metadata(self):
        def _new_offsets():
            # We force a list to ensure the compaction runs to completion,
            # even if our caller decides to not consume everything.
            for nbhe, hdrs in [(n,h) for (o,n,h) in self.iter_compact()]:
                yield nbhe[0], nbhe[1], nbhe[2], hdrs
        return self.iter_email_metadata(iterator=_new_offsets())

    def compact(self):
        return sum(1 for bheh in self.iter_compact())


if __name__ == "__main__":
    import os, sys

    tmbox = b'/tmp/test.mbx'
    os.system(b'cp /home/bre/Mail/mailpile/2013-08.mbx '+tmbox)
    mbox = FormatMbox(None, [tmbox], open('/tmp/test.mbx', 'r+b'))

    for md in mbox.iter_email_metadata():
        print('%s' % md)
        break

    # FIXME

    if 'more' in sys.argv:
        tmbox = b'/tmp/test.mbx'
        os.system(b'cp /home/bre/Mail/mailpile/2013-08.mbx '+tmbox)
        mbox = FormatMbox(None, [tmbox], open(b'/tmp/test.mbx', 'r+b'))

        msgs1 = list(mbox.iter_email_offsets())
        ofs1 = msgs1[len(msgs1)//2]
        key1 = mbox._range_to_key(ofs1[0], ofs1[2])
        msg1 = copy.copy(mbox[key1])
        ofs2 = msgs1[1]
        del mbox[key1]
        assert(msg1 not in (None, b'', ''))
        msgs2 = list(mbox.iter_email_offsets())
        assert(ofs1 not in msgs2)
        assert(ofs2 in msgs2)
        assert(len(msgs1) == len(msgs2)+1)

        mbox.append(msg1)
        for optr, nptr, hdrs in mbox.iter_compact():
            print('%s->%s %s' % (optr, nptr, hdrs[:15]))
        msgs3 = list(mbox.iter_email_offsets())

        assert(len(msgs2)+1 == len(msgs3))

        os.remove(tmbox)

    if False:
        big = 'b/home/bre/Mail/klaki/gmail-2011-11-26.mbx'
        print('%s\n\n' % fs.info(big, details=True))
        msgs = sorted(list(fs.parse_mailbox(big)))
        print('Found %d messages in %s' % (len(msgs), big))
        for msg in msgs[:5] + msgs[-5:]:
            m = msg.parsed()
            f = m['from']
            print('%-38.38s %-40.40s' % (f.fn or f.address, m['subject']))

        for count in range(0, 5):
            i = count * (len(msgs) // 5)
            print('len(msgs[%d]) == %d' % (i, len(dumb_encode_bin(msgs[i], compress=256))))
        print('%s\n' % dumb_encode_bin(msgs[0], compress=None))

        import json, random
        print(json.dumps(
            fs.parse_message(random.choice(msgs)).with_text().with_data(),
            indent=2))

        try:
            print('%s' % fs['/tmp'])
        except IsADirectoryError:
            print('%s' % fs.info('/tmp', details=True))
        print('%s' % fs.info('/lskjdf', details=True))

    print('Tests passed OK')
