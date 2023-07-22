import re
import time
import email.utils
import hashlib
import struct

from .headers import parse_header
from .metadata import Metadata


MAILDIR_TAG_SEP = re.compile(b'[:;-]2,')
HASHED_IDX_MUL = 100000000


def mk_hashed_idx(d, i=0, mul=HASHED_IDX_MUL, mod=None):
    # Note: quick tests indicate that SHA1 seems to be reliably faster
    #       than MD5, in spite of doing "more work"? Since MD5 is no
    #       longer trusted, it may not be getting optimized the same
    #       way anymore?
    d = bytes(d, 'utf-8') if isinstance(d, str) else d
    d = struct.unpack('Q', hashlib.sha1(d).digest()[:8])[0]
    d = (d * mul) + i
    if mod:
        return (d % mod)
    else:
        return d


def mk_packed_idx(d, *ints, count=None, mod=None, _raise=None):
    if (not count) and (len(ints) >= 0x10 or not ints):
        if _raise:
            raise _raise('Too many values')
        ints = []
    if count is None:
        count = len(ints)
        packed = len(ints)
        pos = 0x10
    else:
        packed = 0
        pos = 1
    for c in range(0, count):
        i = ints[c]
        ilen = len('%x' % i)
        if ilen >= 0x10:
            if _raise:
                raise _raise('Too big: %s' % i)
            ilen = i = 0
        packed += (ilen * pos) + (i * pos * 0x10)
        pos *= 0x10 ** (max(1, ilen) + 1)
    if mod and (0 < mod < 64):
        mod = pos * (0x10 ** mod)
    return mk_hashed_idx(d, packed, mul=pos, mod=mod)


def unpack_idx(idx, count=None):
    if count is None:
        count = idx % 0x10
        packed = idx // 0x10
    else:
        packed = idx
    ints = []
    for i in range(0, count):
        ilen = packed % 0x10
        if ilen == 0:
            ints.append(None)
            packed //= 0x100
        else:
            packed //= 0x10
            imul = 0x10 ** ilen
            ints.append(packed % imul)
            packed //= imul
    return ints, packed


def split_maildir_meta(fn):
    fn = bytes(fn, 'utf-8') if isinstance(fn, str) else fn
    return MAILDIR_TAG_SEP.split(fn)


def mk_maildir_idx(fn, i):
    # This uses 8 * 16 = 128 bits of the hash. Plenty!
    return mk_packed_idx(split_maildir_meta(fn)[0], i, count=1, mod=8)


def unpack_maildir_idx(idx):
    ints, _hash = unpack_idx(idx, count=1)
    return ints[0], _hash


def quick_msgparse(obj, beg):
    sep = b'\r\n\r\n' if (b'\r\n' in obj[beg:beg+256]) else b'\n\n'

    hend = obj.find(sep, beg, beg+102400)
    if hend < 0:
        return None
    hend += len(sep)

    # Note: This is fast! We deliberately do not sort, as the order of
    #       headers is one of the things that makes messages unique.
    hdrs = (b'\n'.join(
                h.strip()
                for h in re.findall(Metadata.HEADER_RE, obj[beg:hend]))
        ).replace(b'\r', b'')

    return hend, hdrs


def make_ts_and_Metadata(now, lts, raw_headers, *args):
    # Extract basic metadata. If we fail to find a plausible timestamp,
    # try harder and then make one up that seems plausible, based on the
    # assumption that messages are in chronological order in the mailbox.
    md = Metadata(0, 0, *args)
    if md.timestamp and (md.timestamp > lts/2) and (md.timestamp < now):
        return (max(lts, md.timestamp), md)

    md[md.OFS_TIMESTAMP] = lts

    # Could not parse Date - do we have a From line with a date?
    raw_headers = str(raw_headers, 'latin-1')
    if raw_headers[:5] == 'From ':
        dt = raw_headers.split('\n', 1)[0].split('  ', 1)[-1].strip()
        try:
            tt = email.utils.parsedate_tz(dt)
            ts = int(time.mktime(tt[:9])) - tt[9]
            md[md.OFS_TIMESTAMP] = ts
            return (max(lts, md.timestamp), md)
        except (ValueError, TypeError):
            pass

    # Fall back to scanning the Received headers
    rcvd_ts = []
    for rcvd in parse_header(raw_headers).get('received', []):
        try:
            tt = email.utils.parsedate_tz(rcvd['date'])
            ts = int(time.mktime(tt[:9])) - tt[9]
            rcvd_ts.append(ts)
        except (ValueError, TypeError):
            pass
    if rcvd_ts:
        rcvd_ts.sort()
        md[md.OFS_TIMESTAMP] = rcvd_ts[len(rcvd_ts) // 2]

    return (max(lts, md.timestamp), md)



if __name__ == "__main__":
    mboxsep = b"From foo@example.org at 00:00 UTC\r\n"
    testmsg = b"""\
From: bre@example.org\r\n\
To: bre@example.org\r\n\
Date: Tue, 02 Aug 2022 19:03:42 +0000\r\n\
Subject: ohai\r\n\
\r\n\
This is an e-mail\r\n"""

    for prefix in (b'', mboxsep):
        hend, hdrs = quick_msgparse(prefix + testmsg, 0)

        split_hdrs = testmsg.split(b'\r\n\r\n')[0]
        assert(hend == len(split_hdrs)+len(prefix)+4)

        split_hdrs = split_hdrs.replace(b'\r', b'')
        assert(hdrs == split_hdrs)

    now = time.time()
    lts = now

    # FIXME: Write some tests to verify that our timestamp searching
    #        logic is actually sane and useful.
    # t,m = make_ts_and_Metadata(now, lts, hdrs, [], hdrs)


    # FIXME: Write some direct tests for mk_hashed_idx

    cc = 8
    pi = mk_packed_idx('hello', 1, 2, 12345, 0x123456789abcdef0, mod=cc)
    un = unpack_idx(pi)
    #print('pi1 = %x, len=%d, unpacked=%s' % (pi, len('%x' % pi), un))
    assert(len('%x' % un[1]) <= cc)
    assert(un[0] == [1, 2, 12345, None])

    pi2 = mk_packed_idx('hello', 1, 2, 3, count=3, mod=cc)
    un2 = unpack_idx(pi2, count=3)
    #print('pi2 = %x, unpacked=%s' % (pi2, un2))
    assert(len('%x' % un2[1]) <= cc)
    assert((pi2 % 0x10) != 3)    # Count not taking any space
    assert(un2[0] == [1, 2, 3])  # Int list decoded correctly
    assert(un2[1] == un[1])      # Same hash, same mod

    try:
        pi = mk_packed_idx('hello', 1, 2, 12345, 0x123456789abcdef0,
            _raise=ValueError)
        assert(not 'reached')
    except ValueError:
        pass

    print('Tests passed OK')
