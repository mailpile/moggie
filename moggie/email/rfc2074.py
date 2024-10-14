import binascii
import email.base64mime as email_b
import logging
import re


FOLDING_QUOTED_RE = re.compile(r'\?=\s+=\?', flags=re.DOTALL)
QUOTED_QP = re.compile(r'(=[0-9A-Fa-f][0-9A-Fa-f])')
QUOTED_RE = re.compile(
    r'=\?([^?]+)\?([qb])\?([^?]+)\?=',
    flags=(re.IGNORECASE + re.DOTALL))


def quoted_printable_decode(payload, tostr, in_header=False):
    """
    Parse quoted-printable entities in a payload, using the tostr
    function to convert encoded byte sequences into string values.

    This lets us use a fault-tolerant tostr() that tries different
    approaches to decode.
    """
    if in_header:
        clean = lambda c: c.replace('_', ' ')
    else:
        clean = lambda c: c.replace('=\r\n', '').replace('=\n', '')

    result = []
    cgroups = QUOTED_QP.split(payload)
    while len(cgroups) > 1:
        preamble = clean(cgroups.pop(0))
        if preamble or not result:
            result.extend([(True, preamble), (False, bytearray())])
        result[-1][-1].append(int(cgroups.pop(0)[1:], 16))
    if cgroups:
        result.append((True, clean(cgroups[0])))

    return ''.join((rv if done else tostr(rv)) for done, rv in result)


def rfc2074_unquote(quoted, strict=False):
    text = []
    parts = QUOTED_RE.split(re.sub(FOLDING_QUOTED_RE, '?==?', quoted))
    while parts:
        text.append(parts.pop(0))
        if len(parts) > 3:
            charset = parts.pop(0)
            method = parts.pop(0)
            payload = op = parts.pop(0)

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

            def _d(bytestr):
                for i, cs in enumerate(charsets):
                    try:
                        return str(bytes(bytestr), cs)
                    except Exception as e:
                        if i == len(charsets)-1:
                            logging.debug(
                                'Decode failed for %s (%s)' % (bytestr, quoted))
                            raise

            try:
                if method in ('q', 'Q'):
                    text.append(
                        quoted_printable_decode(payload, _d, in_header=True))

                elif method in ('b', 'B'):
                    padmore = 4 - (len(payload) % 4)
                    if padmore < 4:
                        payload += '==='[:padmore]
                    text.append(_d(email_b.decode(payload)))

                else:
                    text.append(_d(bytes(payload, 'utf-8', errors='replace')))

            except (UnicodeDecodeError, binascii.Error):
                # Silently ignore errors, just return the string as-is.
                text = [op]

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
    print('Tests passed OK')
