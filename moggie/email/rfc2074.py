import binascii
import email.base64mime as email_b
import logging
import re


FOLDING_QUOTED_RE = re.compile(r'\?=\s+=\?', flags=re.DOTALL)
QUOTED_QP = re.compile(r'(=[0-9A-Fa-f][0-9A-Fa-f])')
QUOTED_RE = re.compile(
    r'=\?([^?]+)\?([qb])\?([^?]+)\?=',
    flags=(re.IGNORECASE + re.DOTALL))


def quoted_printable_to_bytearray(payload, header=False):
    if header:
        clean = lambda c: c.replace('_', ' ')
    else:
        clean = lambda c: c.replace('=\r\n', '').replace('=\n', '')

    chars = QUOTED_QP.split(payload)
    payload = bytearray()
    while len(chars) > 1:
        payload.extend(clean(chars.pop(0)).encode('latin-1'))
        payload.append(int(chars.pop(0)[1:], 16))
    if chars:
        payload.extend(clean(chars[0]).encode('latin-1'))
    return payload


def rfc2074_unquote(quoted, strict=False):
    text = []
    parts = QUOTED_RE.split(re.sub(FOLDING_QUOTED_RE, '?==?', quoted))
    while parts:
        text.append(parts.pop(0))
        if len(parts) > 3:
            charset = parts.pop(0)
            method = parts.pop(0)
            payload = op = parts.pop(0)

            if method in ('q', 'Q'):
                # Note: For some reason email.quoprimime insists on returning
                # strings. We want to work with bytes, so do this ourselves.
                payload = quoted_printable_to_bytearray(payload, header=True)

            elif method in ('b', 'B'):
                padmore = 4 - (len(payload) % 4)
                if padmore < 4:
                    payload += '==='[:padmore]
                try:
                    payload = email_b.decode(payload)
                except binascii.Error:
                    # Silently ignore errors, just return the string as-is.
                    payload = payload.encode('latin-1')
                    charset = 'latin-1'
            else:
                payload = payload.encode('latin-1')

            if strict:
                charsets = [charset]
            else:
                # According to https://en.wikipedia.org/wiki/Windows-1252,
                # treating US-ASCII and ISO-8859-1 as if they were the Windows
                # 1252 encoding is probably harmless and likely to help
                # readbility in many cases.
                charsets = [charset]
                if charset in ('iso-8859-1', 'us-ascii', 'utf-8', '', None):
                    charsets = ('utf-8', 'windows-1252')
                elif charset in ('gb2312', 'gbk', 'gb18030'):
                    charsets = ('gb2312', 'gbk', 'gb18030')

            decoded = False
            for cs in charsets:
                try:
                    text.append(str(bytes(payload), cs))
                    decoded = True
                    break
                except (UnicodeDecodeError, LookupError):
                    if cs == charset:
                        logging.debug(
                            'Decode failed for %s (%s)' % (payload, quoted),
                            exc_info=True)
            if not decoded:
                text.append(op)

    return ''.join(text)


def rfc2074_quote(unquoted, linelengths=[72], charset='utf-8'):
    try:
        _ascii = bytes(unquoted, 'us-ascii')
        return unquoted
    except UnicodeEncodeError:
        out = []
        while unquoted:
            maxlen = ((linelengths[0] - len(charset) - len('=??b?=')) * 3) // 4
            for cc in reversed(range(2, maxlen)):
                _utf8 = bytes(unquoted[:cc], charset)
                if len(_utf8) <= maxlen:
                    unquoted = unquoted[cc:]
                    break
            out.append(email_b.header_encode(_utf8, charset=charset))
            if len(linelengths) > 1:
                linelengths.pop(0)
        return ' '.join(out)


if __name__ == '__main__':
    import base64

    test = 'hello verööld'
    test_b64 = '=?utf-8?b?%s?=' % str(base64.b64encode(test.encode('utf-8')).strip(), 'latin-1')
    assert(rfc2074_unquote('hello =?iso-8859-1?q?ver=F6=F6ld?=') == test)
    assert(rfc2074_unquote(test_b64) == test)

    # Make sure we do not explode on invalid UTF-8
    bad_b64 = str(base64.b64encode(b'\xc3\0\0\0').strip(), 'latin-1')
    assert(rfc2074_unquote('=?utf-8?b?%s?=' % bad_b64, strict=True) == bad_b64)

    # Tests from the RFC
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a?= b') == 'a b')
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a?=  =?ISO-8859-1?Q?b?=') == 'ab')
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a_b?=') == 'a b')
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a?= =?ISO-8859-2?Q?_b?=') == 'a b')

    for bad in (
        '=?utf-8?Q?=AF=E4=BB=B6?=',
        '=?utf-8?Q?=BA=A6=E7=9A=84=E9=82=AE=E4=BB=B6=E7=BE=A4=E5=8F=91=E8=BD?=',
        '=?GB2312?B?v6q2kMaxbDM2Mk85MTIzN2w=?='
    ):
        assert(rfc2074_unquote(bad, strict=True) == bad.split('?')[3])

    print('Tests passed OK')
