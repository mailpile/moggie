# Some tools/heuristics for validationg the contents of message headers

def validate_dates(metadata_ts, parsed_email, remote_only=True):
    timestamps = []
    if metadata_ts:
        timestamps.append(int(metadata_ts))
    time_went_backwards = []

    for h in ('received', 'arc-seal'):
        last_ts = 0
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

    for h in ('_DATE_TS', ):
        try:
            timestamps.append(parsed_email.get(h))
        except (KeyError, ValueError):
            pass

    timestamps = sorted(list(set(timestamps)))
    max_diff = timestamps[-1] - timestamps[0]
    info = {
        'delta': max_diff,
        'timestamps': timestamps}
    if time_went_backwards:
        info['time_went_backwards'] = time_went_backwards
    return info

