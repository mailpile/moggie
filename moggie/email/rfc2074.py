import binascii
import email.base64mime as email_b
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


def rfc2074_unquote(quoted):
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

            try:
                text.append(str(bytes(payload), charset))
            except UnicodeDecodeError:
                text.append(op)

    return ''.join(text)


if __name__ == '__main__':
    import base64
    test = 'hello verööld'
    test_b64 = '=?utf-8?b?%s?=' % str(base64.b64encode(test.encode('utf-8')).strip(), 'latin-1')
    assert(rfc2074_unquote('hello =?iso-8859-1?q?ver=F6=F6ld?=') == test)
    assert(rfc2074_unquote(test_b64) == test)

    # Make sure we do not explode on invalid UTF-8
    bad_b64 = str(base64.b64encode(b'\xc3\0\0\0').strip(), 'latin-1')
    assert(rfc2074_unquote('=?utf-8?b?%s?=' % bad_b64) == bad_b64)

    # Tests from the RFC
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a?= b') == 'a b')
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a?=  =?ISO-8859-1?Q?b?=') == 'ab')
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a_b?=') == 'a b')
    assert(rfc2074_unquote('=?ISO-8859-1?Q?a?= =?ISO-8859-2?Q?_b?=') == 'a b')

    print('Tests passed OK')
