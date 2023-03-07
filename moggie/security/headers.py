# Some tools/heuristics for validationg the contents of message headers
import copy
import re
import time


# This should match [1.2.3.4] or [ffff::0001] type addresses, but
# not at the start of the line; the start of the line is usually what
# the server reports in HELO, but the data added later is what the
# SMTP server sees on the wire.
_GSH_IPADDR = re.compile(
    # FIXME: Hande IPv4-as-IPv6 syntax?
    r'[\s]+[\[\(]+(\d+\.\d+\.\d+\.\d+|[\da-fA-F:]+:[\da-fA-F:]+)[\]\)]')


async def validate_smtp_hops(parsed_email, check_dns=True):
    # Notes:
    #
    #    - If there are less than two hops, then this message came
    #      directly from the org which generated the mail. This is
    #      normal, but it rules out the old case of users running a
    #      mail client which sends via. a relay SMTP server.
    #
    #    - ...
    #
    path = []
    info = {'hops': path}

    for hop in (parsed_email.get('received') or []):
        _from = hop.get('from', '')
        if (_from and 'smtp' in hop.get('with', '').lower()
                and ' [::1]' not in _from
                and ' [127.' not in _from):
            path.append({
                'received_ts': hop.get('timestamp'),
                'received_from': _from,
                'received_by': hop.get('by', '') or '(unknown)'})

    # FIXME: Try and extract IP addresses and DNS names.
    #        Check if they actually match?
    for hop in path:
        ip_match = _GSH_IPADDR.search(hop['received_from'])
        if ip_match:
            hop['from_ip'] = ip_match.group(1)
            if check_dns:
                pass  # FIXME: aiodns requests go here

    return info


def validate_dates(metadata_ts, parsed_email, remote_only=True, now=None):
    now = now or time.time()
    timestamps = []
    timezones = []

    if metadata_ts:
        timestamps.append(int(metadata_ts))
    time_went_backwards = []

    for h in ('received', 'arc-seal'):
        last_ts = now
        for hdr in parsed_email.get(h, []):
            if remote_only and h == 'received':
                if ('POP3' in hdr.get('via', '')
                        or 'IMAP' in hdr.get('via', '')
                        or 'localhost' in hdr.get('from', '')
                        or '[127.' in hdr.get('from', '')):
                    next
            date = hdr.get('timestamp') or hdr.get('t')
            if date:
                try:
                    ts = int(date)
                    timestamps.append(ts) 
                    if last_ts and ts > last_ts:
                        time_went_backwards.append(h)
                    last_ts = ts
                except ValueError:
                    pass
            tz = hdr.get('tz')
            if tz is not None:
                timezones.append(tz)

    try:
        timestamps.append(parsed_email['_DATE_TS'])
        timezones.append(parsed_email['_DATE_TZ'])
    except (KeyError, ValueError):
        pass

    timezones = sorted(list(set(timezones)))
    timestamps = sorted(list(set(timestamps)))
    max_diff = timestamps[-1] - timestamps[0]
    info = {
        'delta': max_diff,
        'delta_large': (max_diff > 2*24*3600),
        'delta_tzbug': (0 < max_diff < 24*3600) and (max_diff % 3600 == 0),
        'timezones': timezones,
        'timestamps': timestamps}
    if time_went_backwards:
        info['time_went_backwards'] = time_went_backwards
    return info

