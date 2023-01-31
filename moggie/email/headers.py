import logging
import re
import datetime
from email.utils import encode_rfc2231, formatdate, format_datetime

from .rfc2074 import rfc2074_quote, rfc2074_unquote
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

HEADER_ORDER = {
    'date': 100,
    'subject': 101,
    'from': 102,
    'reply-to': 102,
    'to': 103,
    'cc': 104,
    'bcc': 105,
    'resent-date': 90,
    'resent-from': 92,
    'resent-to': 93,
    'resent-cc': 94,
    'in-reply-to': 99,
    'references': 99,
    'list-id': 20,
    'list-help': 20,
    'message-id': 16,
    'mime-version': 16,
    'content-id': 17,
    'content-length': 17,
    'content-type': 17,
    'content-disposition': 18,
    'content-transfer-encoding': 19}

HEADER_CASEMAP = {
    'mime-version': 'MIME-Version',
    'content-type': 'Content-Type',
    'content-disposition': 'Content-Disposition',
    'content-transfer-encoding': 'Content-Transfer-Encoding',
    'from': 'From',
    'to': 'To',
    'cc': 'Cc',
    'date': 'Date',
    'subject': 'Subject',
    'autocrypt': 'Autocrypt',
    'reply-to': 'Reply-To',
    'in-reply-to': 'In-Reply-To',
    'status': 'Status',
    'references': 'References'}

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


def format_header(hname, data,
        as_timestamp=None, intfmt='%d', floatfmt='%.2f'):
    hname = HEADER_CASEMAP.get(hname.lower(), hname)
    values = []
    sep = (
        ', ' if (hname in ('To', 'Cc')) else
        ' ' if (hname == 'Subject') else
        '; ')
    if not isinstance(data, list):
        data = [data]

    if hname == 'Date':
        as_timestamp = True
    elif hname == 'MIME-Version':
        floatfmt = intfmt = '%.1f'

    ll = [None, 70 - len(hname), 72]
    def _quote_space(txt):
        if ' ' in txt or '\t' in txt:
            return '"%s"' % (txt.replace('"', '\\"'))
        return txt
    def _encode(item):
        if isinstance(item, list):
            rv = []
            for i in item:
                rv.extend(_encode(i))
            return rv
        elif hasattr(item, 'normalized'):
            return [item.normalized()]
        elif isinstance(item, tuple):
            k, v = item
            return ['%s=%s' % (k, _quote_space(_encode(v)[0]))]
        elif isinstance(item, dict):
            return _encode(list(item.items()))

        if len(ll) > 1:
            ll.pop(0)

        if isinstance(item, datetime.datetime):
            return [format_datetime(item)]
        elif isinstance(item, int):
            if as_timestamp:
                return [formatdate(item)]
            else:
                return [intfmt % item]
        elif isinstance(item, float):
            if as_timestamp:
                return [formatdate(item)]
            else:
                return [floatfmt % item]
        elif isinstance(item, str):
            return [rfc2074_quote(item, linelengths=ll)]
        elif isinstance(item, bytes):
            return [rfc2074_quote(str(item, 'utf-8'), linelengths=ll)]
    for item in data:
        values.extend(_encode(item))

    def _fold(txt):
        folds = [txt]
        while len(folds[-1]) > 78:
            if sep in folds[-1][3:(78 - len(sep))]:
                pos = 3 + folds[-1][3:(78 - len(sep))].rindex(sep) + len(sep)
            elif '?= =?' in folds[-1][3:78]:
                pos = 3 + folds[-1][3:78].rindex('?= =?') + 3
            elif ' ' in folds[-1][3:78]:
                pos = 3 + folds[-1][3:78].rindex(' ')
            else:
                pos = 78
            p1, p2 = folds[-1][:pos], folds[-1][pos:]
            folds[-1] = '%s\n ' % p1
            folds.append(p2)

        return ''.join(folds)

    return _fold('%s: %s' % (hname, sep.join(values)))


def format_headers(header_dict, eol='\r\n'):
    header_items = list(header_dict.items())
    header_items.sort(key=lambda k:
        (HEADER_ORDER.get(k[0].lower(), 0), k[0], k[1]))
    return (
        eol.join(format_header(k, v) for k, v in header_items)
        + eol + eol)


if __name__ == '__main__':
    import json
    from .addresses import AddressHeaderParser

    def _assert(val, want=True, msg='assert'):
        if isinstance(want, bool):
            if (not val) == (not want):
                want = val
        if val != want:
            raise AssertionError('%s(%s==%s)' % (msg, val, want))

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

    _assert(format_header('To', [
             AddressHeaderParser('Björn <a@example.org>'),
             AddressHeaderParser('Bjarni Runar b@example.org')]),
        'To: =?utf-8?Q?Bj=C3=B6rn?= <a@example.org>, '
        '"Bjarni Runar" <b@example.org>')

    aedi = 'æði pæði skúmmelaði'
    subject = format_header('Subject', aedi * 10)
    _assert(
        parse_header(subject)['subject'],
        aedi * 10)

    date = format_header('Date', 0, as_timestamp=True)
    _assert(date, 'Date: Thu, 01 Jan 1970 00:00:00 -0000')
    # FIXME: Should this parse to something else?
    _assert(parse_header(date)['date'], 'Thu, 01 Jan 1970 00:00:00 -0000')

    _assert(format_headers({
            'Subject': 'Halló heimur',
            'Date': 0,
            'From': AddressHeaderParser('Bjarni R. E. bre@example.org')[0],
            'MIME-Version': 1,
            'Content-Type': ['multipart/mixed', ('boundary', 'magic123')],
        }, eol='\n'),
        """\
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary=magic123
Date: Thu, 01 Jan 1970 00:00:00 -0000
Subject: =?utf-8?b?SGFsbMOzIGhlaW11cg==?=
From: "Bjarni R. E." <bre@example.org>

""")

    print('Tests passed OK')
