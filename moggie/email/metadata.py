import email.utils
import hashlib
import re
import time

from ..util.dumbcode import dumb_encode_asc, dumb_decode

from .headers import parse_header



class Metadata(list):
    OFS_TIMESTAMP = 0
    OFS_IDX = 1
    OFS_POINTERS = 2
    OFS_HEADERS = 3
    OFS_MORE = 4
    _FIELDS = 5

    # These are the headers we want extracted and stored in metadata.
    # Note the Received headers are omitted, too big and too much noise.
    HEADER_RE = re.compile(b'\n(' +
            b'(?:Date|Message-ID|In-Reply-To|From|To|Cc|Subject):' +
            b'(?:[^\n]+\n\\s+)*[^\n]+' +
        b')',
        flags=(re.IGNORECASE + re.DOTALL))

    FIND_RE = {
        'in-reply-to': re.compile(r'(?:^|\n)in-reply-to:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL)),
        'message-id': re.compile(r'(?:^|\n)message-id:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL)),
        'subject': re.compile(r'(?:^|\n)subject:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL)),
        'date': re.compile(r'(?:^|\n)date:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL)),
        'from': re.compile(r'(?:^|\n)from:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL)),
        'to': re.compile(r'(?:^|\n)to:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL)),
        'cc': re.compile(r'(?:^|\n)cc:\s*([^\n]*)', flags=(re.IGNORECASE + re.DOTALL))}

    FOLDING_QUOTED_RE = re.compile('=\\?\\s+=\\?', flags=re.DOTALL)
    FOLDING_RE = re.compile('\r?\n\\s+', flags=re.DOTALL)

    @classmethod
    def ghost(self, msgid, more=None):
        msgid = msgid if isinstance(msgid, bytes) else bytes(msgid, 'latin-1')
        return Metadata(0, 0,
            Metadata.PTR(0, b'/dev/null', 0, 0, 0),
            b'Message-Id: %s' % msgid,
            more=more)

    class PTR(list):
        IS_MBOX = 0
        IS_MAILDIR = 1
        IS_REMOTE = 1000

        OFS_PTR_TYPE = 0
        OFS_MAILBOX = 1
        OFS_OFFSET = 2
        OFS_HEADER_LENGTH = 3
        OFS_MESSAGE_LENGTH = 4
        _FIELDS = 5

        def __init__(self, ptr_type, mailbox, offset, hlen, mlen):
            if isinstance(mailbox, bytes):
                mailbox = dumb_encode_asc(mailbox)
            list.__init__(self, [
                int(ptr_type), mailbox, int(offset), int(hlen), int(mlen)])

        is_local_file = property(
            lambda s: s.ptr_type in (s.IS_MBOX, s.IS_MAILDIR))

        ptr_type = property(lambda s: s[s.OFS_PTR_TYPE])
        mailbox = property(lambda s: s[s.OFS_MAILBOX])
        offset =  property(lambda s: s[s.OFS_OFFSET])
        header_length  = property(lambda s: s[s.OFS_HEADER_LENGTH])
        message_length = property(lambda s: s[s.OFS_MESSAGE_LENGTH])
        container = property(lambda s: s.get_container())

        def get_container(self):
            mailbox = dumb_decode(self.mailbox)
            if self.ptr_type == self.IS_MAILDIR:
                return mailbox.rsplit(b'/', 1)[0]
            return mailbox

    def __init__(self, ts, idx, ptrs, hdrs, more=None):
        # The encodings here are to make sure we are JSON serializable.
        if isinstance(hdrs, bytes):
            hdrs = str(hdrs, 'latin-1')
        if isinstance(ptrs, self.PTR):
            ptrs = [ptrs]
        if not isinstance(ptrs, list) or len(ptrs) < 1:
            raise ValueError('Invalid PTR')
        for ptr in ptrs:
            if not isinstance(ptr, list) or (len(ptr) != self.PTR._FIELDS):
                raise ValueError('Invalid PTR: %s' % ptr)

        list.__init__(self, [
            ts or 0, idx or 0, ptrs, hdrs.replace('\r', ''),
            more or {}])

        self._raw_headers = {}
        self._parsed = None
        self.thread_id = None
        self.mtime = 0

        if not ts:
            date = self.get_raw_header('Date')
            if date:
                try:
                    self[0] = int(time.mktime(email.utils.parsedate(date)))
                except (ValueError, TypeError):
                    pass

    timestamp      = property(lambda s: s[s.OFS_TIMESTAMP])
    idx            = property(lambda s: s[s.OFS_IDX])
    pointers       = property(lambda s: [Metadata.PTR(*p) for p in sorted(s[s.OFS_POINTERS])])
    more           = property(lambda s: s[s.OFS_MORE])
    headers        = property(lambda s: s[s.OFS_HEADERS])
    uuid_asc       = property(lambda s: dumb_encode_asc(s.uuid))
    uuid           = property(lambda s: hashlib.sha1(
            b''.join(sorted(s.headers.strip().encode('latin-1').splitlines()))
        ).digest())

    def __str__(self):
        return ('%d=%s@%s %d %s\n%s\n' % (
            self.idx,
            self.uuid_asc,
            self.pointers,
            self.timestamp,
            self.more,
            self.headers))

    def set(self, key, value):
        self.more[key] = value
        self._parsed = None

    def get(self, key, default=None):
        self.more.get(key, default)

    def add_pointers(self, pointers):
        combined = self.pointers
        by_container = dict((p.container, p) for p in combined)
        for mp in (Metadata.PTR(*p) for p in pointers):
            replacing = by_container.get(mp.container)
            if replacing:
                combined.remove(replacing)
            combined.append(mp)
        self[self.OFS_POINTERS] = combined

    def get_raw_header(self, header):
        try:
            header = header.lower()
            if header not in self._raw_headers:
                fre = self.FIND_RE[header]
                self._raw_headers[header] = fre.search(self.headers).group(1)
            return self._raw_headers[header]
        except (AttributeError, IndexError, TypeError):
            return None

    def parsed(self, force=False):
        if force or self._parsed is None:
            self._parsed = {
                'ts': self.timestamp,
                'ptrs': self.pointers,
                'uuid': self.uuid}
            self._parsed.update(parse_header(self.headers))
            self._parsed.update(self.more)
        return self._parsed


if __name__ == "__main__":
    import json
    from ..util.dumbcode import dumb_decode

    # We are seeding the compression engine with known common strings,
    # and this gets base64 encoded - groups of 3 chars become groups
    # of four; thus the weird name. I may be overthinking it!
    mbx_path = b'/home/varmaicurmbx'

    md1 = Metadata(0, 0, Metadata.PTR(0, mbx_path, 0, 100, 200), """\
From: Bjarni <bre@example.org>\r
To: bre@example.org\r
Subject: This is Great\r\n""", {'tags': 'inbox,unread,sent'})
    md2 = Metadata(0, 0, [[1, b'/tmp/cur/test.mbx', 0, 100, 200]], """\
To: bre@example.org
From: Bjarni <bre@example.org>
Subject: This is Great""")

    #print('%s == %s' % (md1.uuid_asc, json.dumps(md1)))
    #print('%s' % (md1.parsed(),))

    assert(md1.uuid == md2.uuid)
    assert(md1.pointers[0].container == dumb_decode(md1.pointers[0].mailbox))
    assert(md2.pointers[0].container != md2.pointers[0].mailbox)

    # Make sure that adding pointers works sanely; the first should
    # be added, the second should merely update the pointer list.
    assert(md1.pointers[0].header_length == 100)
    md1.add_pointers([Metadata.PTR(0, b'/dev/null', 0, 200, 200)])
    md1.add_pointers([Metadata.PTR(0, mbx_path, 0, 300, 300)])
    assert(len(md1.pointers) == 2)
    assert(md1.pointers[0].header_length == 200)
    assert(md1.pointers[1].header_length == 300)
    assert(md1.pointers[1].container == mbx_path)
    md1.add_pointers([(0, b'/dev/null', 0, 100, 200)])
    assert(len(md1.pointers) == 2)

    print("Tests passed OK")
