import datetime
import os


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
