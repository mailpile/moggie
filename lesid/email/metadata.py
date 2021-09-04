import email.utils
import hashlib
import re
import time

from ..util.dumbcode import dumb_encode_asc

from .headers import parse_header


class Metadata(list):
    TIMESTAMP = 0
    MAILBOX = 1
    OFFSET = 2
    HEADER_LENGTH = 3
    MESSAGE_LENGTH = 4
    HEADERS = 5
    MORE = 6
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
        self._parsed = None

    def __str__(self):
        return ('%s=%s@%d/%d/%d %d %s\n%s\n' % (
            self.uuid(),
            self[self.MAILBOX],
            self[self.OFFSET],
            self[self.HEADER_LENGTH],
            self[self.MESSAGE_LENGTH],
            self[self.TIMESTAMP],
            self[self.MORE],
            self[self.HEADERS]))

    def uuid(self):
        return hashlib.sha1(self[self.HEADERS].encode('latin-1')).hexdigest()

    def set(self, key, value):
        self[self.MORE][key] = value
        self._parsed = None

    def get(self, key, default=None):
        self[self.MORE].get(key, default)

    def get_raw_header(self, header):
        try:
            fre = self.FIND_RE[header.lower()]
            return fre.search(self[self.HEADERS]).group(1)
        except (AttributeError, IndexError):
            return None

    def parsed(self, force=False):
        if force or self._parsed is None:
            self._parsed = {
                'ts': self[self.TIMESTAMP],
                'ptr': [self[self.MAILBOX], self[self.OFFSET], self[self.MESSAGE_LENGTH]],
                'uuid': self.uuid()}
            self._parsed.update(parse_header(self[self.HEADERS]))
            self._parsed.update(self[self.MORE])
        return self._parsed


if __name__ == "__main__":

    # FIXME: Write some tests

    print("Tests passed OK")
