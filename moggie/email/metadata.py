import email.utils
import hashlib
import re
import time

from ..util.dumbcode import dumb_encode_asc

from .headers import parse_header


class Metadata(list):
    OFS_TIMESTAMP = 0
    OFS_MAILBOX = 1
    OFS_OFFSET = 2
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

    def __init__(self, ts, fn, ofs, hlen, mlen, hdrs, more=None):
        # The encodings here are to make sure we are JSON serializable.
        if isinstance(fn, bytes):
            fn = dumb_encode_asc(fn)
        if isinstance(hdrs, bytes):
            hdrs = str(hdrs, 'latin-1')
        list.__init__(self, [ts or 0, fn, ofs, hlen, mlen, hdrs, more or {}])
        if not ts:
            date = self.get_raw_header('Date')
            if date:
                try:
                    self[0] = int(time.mktime(email.utils.parsedate(date)))
                except (ValueError, TypeError):
                    pass
        self._raw_headers = {}
        self._parsed = None

        self.idx = None
        self.thread_id = None
        self.mtime = 0

    timestamp      = property(lambda s: s[s.OFS_TIMESTAMP])
    mailbox        = property(lambda s: s[s.OFS_MAILBOX])
    offset         = property(lambda s: s[s.OFS_OFFSET])
    header_length  = property(lambda s: s[s.OFS_HEADER_LENGTH])
    message_length = property(lambda s: s[s.OFS_MESSAGE_LENGTH])
    more           = property(lambda s: s[s.OFS_MORE])
    headers        = property(lambda s: s[s.OFS_HEADERS])
    uuid           = property(
        lambda s: hashlib.sha1(s.headers.encode('latin-1')).hexdigest())

    def __str__(self):
        return ('%s=%s@%d/%d/%d %d %s\n%s\n' % (
            self.uuid,
            self.mailbox,
            self.offset,
            self.header_length,
            self.message_length,
            self.timestamp,
            self.more,
            self.headers))

    def set(self, key, value):
        self.more[key] = value
        self._parsed = None

    def get(self, key, default=None):
        self.more.get(key, default)

    def get_raw_header(self, header):
        try:
            header = header.lower()
            if header not in self._raw_headers:
                fre = self.FIND_RE[header]
                self._raw_headers[header] = fre.search(self.headers).group(1)
            return self._raw_headers[header]
        except (AttributeError, IndexError):
            return None

    def parsed(self, force=False):
        if force or self._parsed is None:
            self._parsed = {
                'ts': self.timestamp,
                'ptr': [self.mailbox, self.offset, self.message_length],
                'uuid': self.uuid}
            self._parsed.update(parse_header(self.headers))
            self._parsed.update(self.more)
        return self._parsed


if __name__ == "__main__":

    # FIXME: Write some tests

    print("Tests passed OK")
