import datetime
import os
import re
import time


_time_multipliers = {
    's':  1,
    'M':  60,
    'h':  60 * 60,
    'H':  60 * 60,
    'd':  60 * 60 * 24,
    'w':  60 * 60 * 24 * 7,
    'm': (60 * 60 * 24 * (30 + 31)) // 2,
    'y': (60 * 60 * 24 * (365 + 365 + 365 + 366)) // 4}



def seconds_to_friendly_time(secs, parts=1, min_secs=60):
    suffix, mul = 's', 1
    for ns, nmul in _time_multipliers.items():
        if secs < nmul:
            break
        suffix, mul = ns, nmul
    amt = '%d%s' % (secs // mul, suffix)
    if (parts > 1) and (secs % mul > min_secs):
        amt += ' ' + seconds_to_friendly_time(secs % mul, parts=parts-1)
    return amt

def friendly_time_to_seconds(word):
    mul = _time_multipliers.get(word[-1:])
    if not mul:
        return int(word)
    return int(word[:-1]) * mul

def friendly_time_ago_to_timestamp(word, now=None):
    if now is None:
        now = int(time.time())

    if word[-1:] in ('m', 'y', 'Y'):
        dt_now = datetime.datetime.utcfromtimestamp(now)
        months = int(word[:-1]) if (word[-1] == 'm') else 0
        years = int(word[:-1]) if (word[-1] in ('y', 'Y')) else 0
        years += months // 12
        months %= 12
        if months > dt_now.month:
            months -= 12
            years += 1

        return int(datetime.datetime(
            dt_now.year - years,
            dt_now.month - months,
            dt_now.day,
            dt_now.hour,
            dt_now.minute,
            dt_now.second).timestamp())

    return now - friendly_time_to_seconds(word)

def friendly_caps(word):
    parts = re.split('[\\s_\\.-]', word)
    return ' '.join('%s%s' % (p[:1].upper(), p[1:]) for p in parts if p)

def friendly_date(ts):
    if ts is None:
        return '?'
    dt = datetime.datetime.fromtimestamp(int(ts))
    return '%4.4d-%2.2d-%2.2d' % (dt.year, dt.month, dt.day)

def friendly_datetime(ts):
    if ts is None:
        return '?'
    dt = datetime.datetime.fromtimestamp(int(ts))
    return '%4.4d-%2.2d-%2.2d %2.2d:%2.2d' % (
        dt.year, dt.month, dt.day, dt.hour, dt.minute)

def friendly_bytes(size):
    if size is None:
        return '?'
    if size >= 1024*1024*1024:
        return '%dG' % (size // (1024*1024*1024))
    if size >= 1024*1024:
        return '%dM' % (size // (1024*1024))
    if size >= 1024:
        return '%dK' % (size // 1024)
    return '%d' % size

def friendly_path(path, maxlen=40):
    path = str(path, 'utf-8') if isinstance(path, bytes) else path
    if len(path) <= maxlen:
        return path

    if path.startswith('imap:'):
        # FIXME: Think more about how/when we drop user@ parts
        fmt = 'imap:.../%s'
        path = path[5:]
    else:
        fmt = '.../%s'

    parts = path.split(os.path.sep)
    while len(path) > maxlen:
        parts.pop(0)
        path = '.../%s' % os.path.join(*parts)
    return path

def friendly_date_formats(ts=None):
    now_ts = int(ts or time.time())
    fdate = friendly_date(now_ts)
    return {
        'yyyy_mm_dd': fdate,
        'yyyy_mm': fdate.rsplit('-', 1)[0],
        'yyyy': fdate.split('-', 1)[0],
        'ts': now_ts}
