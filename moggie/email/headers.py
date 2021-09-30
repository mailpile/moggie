import re

from .rfc2074 import rfc2074_unquote
from .addresses import AddressHeaderParser


FOLDING_RE = re.compile(r'\r?\n\s+', flags=re.DOTALL)

SINGLETONS = (
    '_mbox_separator',
    'content-disposition',
    'content-length',
    'content-type',
    'date',
    'errors-to',
    'from',
    'mime-version',
    'reply-to',
    'subject',
    'user-agent',
    'x-mailer')

TEXT_HEADERS = ('subject',)

ADDRESS_HEADERS = (
    'apparently-to',
    'bcc'
    'cc',
    'errors-to',
    'from',
    'to',
    'reply-to',
    'resent-bcc',
    'resent-cc',
    'resent-from',
    'resent-reply-to',
    'resent-sender',
    'resent-to',
    'sender')


def parse_header(raw_header):
    """
    This will parse an e-mail header into a JSON-serializable dictionary
    of useful information.
    """
    if isinstance(raw_header, bytes):
        raw_header = str(raw_header, 'latin-1')

    unfolded = re.sub(FOLDING_RE, '', raw_header)
    headers = {}
    order = []
    for ln in unfolded.splitlines():
        try:
            if ln[:1] == '_':
                raise ValueError('Illegal char in header name')
            hdr, val = ln.split(':', 1)
            hdr = hdr.lower()
            val = val.strip()
        except ValueError:
            val = ln.strip()
            if not val:
                continue
            if val[:5] == 'From ':
                hdr = '_mbox_separator'
            else:
                hdr = '_invalid'
                headers['_has_errors'] = True

        if hdr in SINGLETONS and hdr in headers:
            hdr = '_duplicate-' + hdr
            headers['_has_errors'] = True

        order.append(hdr)

        if hdr in ADDRESS_HEADERS:
            headers[hdr] = headers.get(hdr, []) + AddressHeaderParser(val)

        elif hdr in TEXT_HEADERS:
            headers[hdr] = headers.get(hdr, []) + [rfc2074_unquote(val)]

        else:
            headers[hdr] = headers.get(hdr, []) + [val]

    headers['_order'] = order
    for hdr in SINGLETONS:
        if hdr in headers:
            if headers[hdr]:
                headers[hdr] = headers[hdr][0]
            else:
                del headers[hdr]

    return headers


if __name__ == '__main__':
    import json

    parse = parse_header(b"""\
From something at somedate
Received: from foo by bar
Received: from bar by baz
From: Bjarni R. Einarsson <bre@example.org>
To: spamfun@example.org
To: duplicate@example.org
X-Junk: 123
Subject: =?utf-8?b?SGVsbG8gd29ybGQ=?= is =?utf-8?b?SGVsbG8gd29ybGQ=?=

""")
    #print('%s' % json.dumps(parse, indent=1))

    assert(json.dumps(parse))
    assert(parse['to'][0].fn == '')
    assert(parse['to'][0].address == 'spamfun@example.org')
    assert(parse['from'].fn == 'Bjarni R. Einarsson')
    assert(parse['from'].address == 'bre@example.org')
    assert(parse['subject'] == 'Hello world is Hello world')
    assert(len(parse['to']) == 2)
    assert(len(parse['received']) == 2)
    assert(not parse.get('_has_errors'))

    print('Tests passed OK')
