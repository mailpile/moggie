import email.utils
import hashlib
import re
import time

from ..storage.formats import tag_path, split_tagged_path
from ..util.dumbcode import dumb_decode, dumb_encode_asc, dumb_encode_bin
from .headers import parse_header



class Metadata(list):
    OFS_TIMESTAMP = 0
    OFS_IDX = 1
    OFS_POINTERS = 2
    OFS_HEADERS = 3
    OFS_DATA_TYPE = 4  # We overload the Parent ID for parent-less data types
    OFS_PARENT_ID = 4
    OFS_THREAD_ID = 5
    OFS_MORE = 6
    _FIELDS = 7

    TYPE_EMAIL = 'email'      # RFC 2822 message
    TYPE_EVENT = 'event'      # Calendar entry (ical)
    TYPE_CONTACT = 'contact'  # Contact information (vcard)
    TYPE_MAP = {
       -1: TYPE_CONTACT,
       -2: TYPE_EVENT}

    # These are the headers we want extracted and stored in metadata.
    # Note the Received headers are omitted, too big and too much noise.
    HEADER_RE = re.compile(b'(?:^|\n)(' +
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
            Metadata.PTR(0, b'/dev/null', 0),
            b'Message-ID: %s' % msgid,
            parent_id=0,
            thread_id=0,
            more=more)

    class PTR(list):
        IS_FS = 0
        IS_REMOTE = 1000

        OFS_PTR_TYPE = 0
        OFS_PTR_PATH = 1
        OFS_MESSAGE_LENGTH = 2
        _FIELDS = 3

        def __init__(self, ptr_type, ptr_path, mlen):
            if isinstance(ptr_path, bytes):
                ptr_path = dumb_encode_asc(ptr_path)
            list.__init__(self, [int(ptr_type), ptr_path, int(mlen)])

        is_local_file = property(
            lambda s: s.ptr_type in (s.IS_FS,))

        ptr_type = property(lambda s: s[s.OFS_PTR_TYPE])
        ptr_path = property(lambda s: s[s.OFS_PTR_PATH])
        message_length = property(lambda s: s[s.OFS_MESSAGE_LENGTH])
        container = property(lambda s: s.get_container())

        def get_container(self):
            return split_tagged_path(dumb_decode(self.ptr_path))[0]

    def __init__(self, ts, idx, ptrs, hdrs, parent_id=None, thread_id=None, more=None):
        # The encodings here are to make sure we are JSON serializable.
        if isinstance(hdrs, bytes):
            hdrs = str(hdrs, 'latin-1')
        if isinstance(ptrs, self.PTR):
            ptrs = [ptrs]
        if not isinstance(ptrs, list):
            raise ValueError('Invalid PTR')
        for ptr in ptrs:
            if not isinstance(ptr, list) or (len(ptr) != self.PTR._FIELDS):
                raise ValueError('Invalid PTR: %s' % ptr)

        list.__init__(self, [
            ts or 0, idx or 0, ptrs, hdrs.replace('\r', ''),
            parent_id, thread_id, more or {}])

        self._raw_headers = {}
        self._parsed = None
        self.mtime = 0

        if not ts:
            date = self.get_raw_header('Date')
            if date:
                try:
                    tt = email.utils.parsedate_tz(date)
                    self[0] = int(time.mktime(tt[:9])) - tt[9]
                except (ValueError, TypeError):
                    pass

    timestamp      = property(lambda s: s[s.OFS_TIMESTAMP])
    idx            = property(lambda s: s[s.OFS_IDX])
    data_type      = property(lambda s: s.TYPE_MAP.get(s[s.OFS_DATA_TYPE], s.TYPE_EMAIL))
    pointers       = property(lambda s: [Metadata.PTR(*p) for p in sorted(s[s.OFS_POINTERS])])
    parent_id      = property(
                         lambda s: s[s.OFS_PARENT_ID] or s[s.OFS_IDX],
                         lambda s, v: s.__setitem__(s.OFS_PARENT_ID, v))
    thread_id      = property(
                         lambda s: s[s.OFS_THREAD_ID] or s[s.OFS_IDX],
                         lambda s, v: s.__setitem__(s.OFS_THREAD_ID, v))
    more           = property(lambda s: s[s.OFS_MORE])
    headers        = property(lambda s: s[s.OFS_HEADERS])
    uuid_asc       = property(lambda s: dumb_encode_asc(s.uuid))
    uuid           = property(lambda s: hashlib.sha1(
            b''.join(sorted(s.headers.strip().encode('latin-1').splitlines()))
        ).digest())

    def __str__(self):
        return ('%d=%s@%s %d %d/%d %s\n%s\n' % (
            self.idx,
            self.uuid_asc,
            self.pointers,
            self.timestamp,
            self.parent_id,
            self.thread_id,
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

    @classmethod
    def FromParsed(cls, p):
        # FIXME: This is incomplete
        return cls(p['ts'], p['idx'], p['ptrs'], p['raw_headers'])

    def parsed(self, force=False):
        if force or self._parsed is None:
            self._parsed = {
                'ts': self.timestamp,
                'idx': self.idx,
                'data_type': self.data_type,
                'ptrs': self.pointers,
                'raw_headers': self.headers,
                'uuid': self.uuid_asc}
            if self._parsed['data_type'] == self.TYPE_EMAIL:
                self._parsed.update({
                    'parent_id': self.parent_id,
                    'thread_id': self.thread_id})
            self._parsed.update(parse_header(self.headers))
            self._parsed.update(self.more)
            self._parsed['_MORE'] = list(self.more.keys())
        return self._parsed

    def get_dkim_status(self):
        """
        Returns a Unix timestamp for when signatures were validated, and
        an array of booleans, each corrosponding to whether the nth DKIM
        signature validated. Returns (None, []) if no info is available.
        """
        stats, ts = (self.get('dkim') or ':').split(':')
        if stats:
            return int(ts, 16), [
                True if (t == 't') else False
                for t in self.more.get('dkim', '')]
        else:
            return None, []

    def set_dkim_status(self, status, ts=None):
        ts = int(ts or time.time())
        sl = ''.join('t' if t else 'f' for t in status)
        self.set('dkim', '%x:%s' % (ts, sl))


if __name__ == "__main__":
    import json

    mbx_path = [b'/home/varmaicur.mbx', (b'mx', b'?0-100')]
    mdir_path = [b'/tmp', (b'md', b'/msgid')]

    print('%s' % tag_path(*mbx_path))
    print('%s' % tag_path(*mdir_path))

    md1 = Metadata(0, 0, Metadata.PTR(0, tag_path(*mbx_path), 200), """\
From: Bjarni <bre@example.org>\r
To: bre@example.org\r
Subject: This is Great\r\n""", 0, 0, {'tags': 'inbox,unread,sent'})

    md2 = Metadata(0, 0, [[0, dumb_encode_asc(tag_path(*mdir_path)), 200]], """\
To: bre@example.org
From: Bjarni <bre@example.org>
Subject: This is Great""")

    for md in (md1, md2):
        md_enc = dumb_encode_bin(md)
        print('%s == [%d] %s' % (md.uuid_asc, len(md_enc), md_enc))
        print('%s' % (md.parsed(),))

    assert(md1.uuid == md2.uuid)
    assert(md1.pointers[0].container == mbx_path[0])
    assert(md2.pointers[0].container == mdir_path[0])

    # Make sure that adding pointers works sanely; the first should
    # be added, the second should merely update the pointer list.
    md1.add_pointers([Metadata.PTR(0, b'/dev/null', 200)])
    md1.add_pointers([Metadata.PTR(0, tag_path(*mbx_path), 300)])
    assert(len(md1.pointers) == 2)
    assert(md1.pointers[1].container == mbx_path[0])
    md1.add_pointers([(0, b'/dev/null', 200)])
    assert(len(md1.pointers) == 2)

    print("Tests passed OK")
