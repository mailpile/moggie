import re
import time
import email.utils

from .headers import parse_header
from .metadata import Metadata


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
            ts = int(time.mktime(email.utils.parsedate(dt)))
            md[md.OFS_TIMESTAMP] = ts
            return (max(lts, md.timestamp), md)
        except (ValueError, TypeError):
            pass

    # Fall back to scanning the Received headers
    rcvd_ts = []
    for rcvd in parse_header(raw_headers).get('received', []):
        try:
            tail = rcvd.split(';')[-1].strip()
            rcvd_ts.append(int(time.mktime(email.utils.parsedate(tail))))
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

    print('Tests passed OK')
