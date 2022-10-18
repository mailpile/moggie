import logging
import re

from .rfc2074 import rfc2074_unquote
from .addresses import AddressHeaderParser


FOLDING_RE = re.compile(r'\r?\n\s+', flags=re.DOTALL)

SINGLETONS = (
    '_mbox_separator',
    'content-transfer-encoding',
    'content-disposition',
    'content-length',
    'content-type',
    'content-id',
    'date',
    'errors-to',
    'from',
    'resent-from',
    'x-original-from',
    'message-id',
    'mime-version',
    'reply-to',
    'return-path',
    'subject',
    'user-agent',
    'x-mailer')

TEXT_HEADERS = ('subject',)

ADDRESS_HEADERS = (
    'apparently-to',
    'bcc',
    'cc',
    'errors-to',
    'from',
    'to',
    'reply-to',
    'resent-bcc',
    'resent-cc',
    'resent-from',
    'x-original-from',
    'resent-reply-to',
    'resent-sender',
    'resent-to',
    'sender')

HEADERS_WITH_PARAMS = (
    'content-type',
    'content-disposition')

HWP_CONTENT_TYPE_RE = re.compile(r'^([a-zA-Z0-9_-]+\/[a-zA-Z0-9_\.-]+)', flags=re.DOTALL)
HWP_VALUE_RE = re.compile(r'^([^;]+)', flags=re.DOTALL)
HWP_TOKEN_RE = re.compile(r'^([a-zA-Z0-9_-]+)', flags=re.DOTALL)
HWP_PARAM_RE = re.compile(r'(;\s*([a-zA-Z0-9_-]+)=([a-zA-Z0-9_\.-]+|\"(?:\\.|[^"\\]+)+\"))', flags=re.DOTALL)
HWP_COMMENT_RE = re.compile(r'^(;?\s*\(([^\(]*)\))', flags=re.DOTALL)


def parse_parameters(hdr, value_re=HWP_VALUE_RE):
    """
    This will parse a typical value-with-parameters into a descriptive
    dictionary. The algorithm does not preserve white-space, and is a
    best-effort algorithm which puts comments and unparsable junk into
    parameters named _COMMENT and _JUNK respectively.
    """
    ohdr = hdr
    m0 = value_re.match(hdr)
    if not m0:
        return [None, {'_JUNK': hdr}]

    params = {}
    m0 = m0.group(0)
    hdr = hdr[len(m0):]
    while hdr:
        p = HWP_PARAM_RE.match(hdr)
        if p:
            hdr = hdr[len(p.group(0)):]
            val = p.group(3)
            if val[:1] == '"':
                try:
                    val = bytes(val[1:-1], 'latin-1').decode('unicode-escape')
                except UnicodeDecodeError:
                    logging.error('UNDECODABLE: %s in %s' % (val, ohdr))
                    raise
            params[p.group(2).lower()] = rfc2074_unquote(val)
        else:
            c = HWP_COMMENT_RE.match(hdr)
            if c:
                cmatch = c.group(0)
                params['_COMMENT'] = c.group(2)
                hdr = hdr[len(cmatch):]
                if cmatch[:1] == ';':
                    hdr = '; ' + hdr
            else:
                params['_JUNK'] = hdr
                break

    return [m0, params]


def parse_content_type(hdr):
    ct, params = parse_parameters(hdr, HWP_CONTENT_TYPE_RE)
    if ct:
        ct = ct.lower()
    return [ct, params]


def parse_header(raw_header):
    """
    This will parse an e-mail header into a JSON-serializable dictionary
    of useful information.
    """
    if isinstance(raw_header, bytes):
        raw_header = str(raw_header, 'latin-1')

    # FIXME: This was '' - which is correct?
    unfolded = re.sub(FOLDING_RE, ' ', raw_header)

    headers = {}
    order = []
    first = True
    for ln in unfolded.splitlines():
        try:
            if first and ln[:5] == 'From ':
                hdr = '_mbox_separator'
                val = ln.strip()
            else:
                if ln[:1] == '_':
                    raise ValueError('Illegal char in header name')
                hdr, val = ln.split(':', 1)
                hdr = hdr.lower()
                val = val.strip()
        except ValueError:
            val = ln.strip()
            if not val:
                continue
            hdr = '_invalid'
            headers['_has_errors'] = True
        first = False

        if hdr in SINGLETONS and hdr in headers:
            hdr = '_duplicate-' + hdr
            headers['_has_errors'] = True

        order.append(hdr)

        if hdr in ADDRESS_HEADERS:
            headers[hdr] = headers.get(hdr, []) + AddressHeaderParser(val)

        elif hdr in TEXT_HEADERS:
            headers[hdr] = headers.get(hdr, []) + [rfc2074_unquote(val)]

        elif hdr == 'content-type':
            headers[hdr] = headers.get(hdr, []) + [parse_content_type(val)]

        elif hdr in HEADERS_WITH_PARAMS:
            headers[hdr] = headers.get(hdr, []) + [parse_parameters(val)]

        else:
            headers[hdr] = headers.get(hdr, []) + [val]

    headers['_ORDER'] = order
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
Subject: =?utf-8?b?SGVsbG8gd29ybGQ=?= is
 =?utf-8?b?SGVsbG8gd29ybGQ=?=

""")
    #print('%s' % json.dumps(parse, indent=1))

    assert(json.dumps(parse))
    assert(parse['_mbox_separator'] == 'From something at somedate')
    assert(parse['to'][0].fn == '')
    assert(parse['to'][0].address == 'spamfun@example.org')
    assert(parse['from'].fn == 'Bjarni R. Einarsson')
    assert(parse['from'].address == 'bre@example.org')
    assert(parse['subject'] == 'Hello world is Hello world')
    assert(len(parse['to']) == 2)
    assert(len(parse['received']) == 2)
    assert(not parse.get('_has_errors'))

    p0v, p0p = parse_parameters('text/plain; charset=us-ascii (Ugh Ugh)')
    assert(p0v == 'text/plain')
    assert(p0p['charset'] == 'us-ascii')
    assert(p0p['_COMMENT'] == 'Ugh Ugh')
    assert('_JUNK' not in p0p)

    p1v, p1p = parse_parameters('text/plain; charset=us ascii')
    assert(p1v == 'text/plain')
    assert(p1p['charset'] == 'us')
    assert(p1p['_JUNK'] == ' ascii')
    assert('_COMMENT' not in p1p)

    p2v, p2p = parse_parameters('multipart/x-mixed; charset="us ascii"')
    assert(p2v == 'multipart/x-mixed')
    assert(p2p['charset'] == 'us ascii')
    assert('_JUNK' not in p2p)
    assert('_COMMENT' not in p2p)

    p3v, p3p = parse_content_type('multipart/x-mixed;(Yuck) CHARSET="us ascii"')
    assert(p3v == 'multipart/x-mixed')
    assert(p3p['charset'] == 'us ascii')
    assert(p3p['_COMMENT'] == 'Yuck')
    assert('_JUNK' not in p2p)

    p4v, p4p = parse_parameters('multipart/mixed')
    assert(p4v == 'multipart/mixed')

    p5v, p5p = parse_content_type('invalid data garbage')
    assert(p5v is None)
    assert(p5p['_JUNK'] == 'invalid data garbage')

    p6v, p6p = parse_parameters('okay; filename="Encryption key for \\"nobody@example.org\\".html"')
    assert(p6p['filename'] == 'Encryption key for "nobody@example.org".html')
    assert('_JUNK' not in p6p)

    print('Tests passed OK')
