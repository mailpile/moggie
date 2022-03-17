import email.utils
import hashlib
import re
import time

from ..util.dumbcode import dumb_encode_asc

from .headers import parse_header



class Metadata(list):
    OFS_TIMESTAMP = 0
    OFS_IDX = 1
    OFS_POINTERS = 2
    OFS_HEADER_LENGTH = 3
    OFS_MESSAGE_LENGTH = 4
    OFS_HEADERS = 5
    OFS_MORE = 6
    _FIELDS = 7

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
            Metadata.PTR(0, b'/dev/null', 0), 0, 0,
            b'Message-Id: %s' % msgid,
            more=more)

    class PTR(list):
        IS_MBOX = 0
        IS_MAILDIR = 1
        IS_REMOTE = 1000

        OFS_PTR_TYPE = 0
        OFS_MAILBOX = 1
        OFS_OFFSET = 2
        _FIELDS = 3

        def __init__(self, ptr_type, mailbox, offset):
            if isinstance(mailbox, bytes):
                mailbox = dumb_encode_asc(mailbox)
            list.__init__(self, [int(ptr_type), mailbox, int(offset)])

        ptr_type = property(lambda s: s[s.OFS_PTR_TYPE])
        mailbox = property(lambda s: s[s.OFS_MAILBOX])
        offset =  property(lambda s: s[s.OFS_OFFSET])

    def __init__(self, ts, idx, ptrs, hlen, mlen, hdrs, more=None):
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
            ts or 0, idx or 0, ptrs, hlen, mlen, hdrs.replace('\r', ''),
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
    header_length  = property(lambda s: s[s.OFS_HEADER_LENGTH])
    message_length = property(lambda s: s[s.OFS_MESSAGE_LENGTH])
    more           = property(lambda s: s[s.OFS_MORE])
    headers        = property(lambda s: s[s.OFS_HEADERS])
    uuid_asc       = property(lambda s: dumb_encode_asc(s.uuid))
    uuid           = property(lambda s: hashlib.sha1(
            b''.join(sorted(s.headers.strip().encode('latin-1').splitlines()))
        ).digest())

    def __str__(self):
        return ('%d=%s:%d/%d@%s %d %s\n%s\n' % (
            self.idx,
            self.uuid_asc,
            self.header_length,
            self.message_length,
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
        for mp in (Metadata.PTR(*p) for p in pointers):
            if mp not in combined:
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

    md1 = Metadata(0, 0, Metadata.PTR(0, b'/tmp/test.mbx', 0), 100, 200, """\
From: Bjarni <bre@example.org>\r
To: bre@example.org\r
Subject: This is Great\r\n""")
    md2 = Metadata(0, 0, [[0, b'/tmp/test.mbx', 0]], 100, 200, """\
To: bre@example.org
From: Bjarni <bre@example.org>
Subject: This is Great""")

    #print('%s == %s' % (md1.uuid_asc, json.dumps(md1)))
    #print('%s' % (md1.parsed(),))

    assert(md1.uuid == md2.uuid)
    assert(str(md1)[:70] == str(md2)[:70])

    # Make sure that adding pointers works sanely
    md1.add_pointers([Metadata.PTR(0, b'/dev/null', 0)])
    assert(len(md1.pointers) == 2)
    md1.add_pointers([(0, b'/dev/null', 0)])
    assert(len(md1.pointers) == 2)

    print("Tests passed OK")
