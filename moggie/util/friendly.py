import datetime

def friendly_date(ts):
    dt = datetime.datetime.fromtimestamp(int(ts))
    return '%4.4d-%2.2d-%2.2d' % (dt.year, dt.month, dt.day)

def friendly_datetime(ts):
    dt = datetime.datetime.fromtimestamp(int(ts))
    return '%4.4d-%2.2d-%2.2d %2.2d:%2.2d' % (
        dt.year, dt.month, dt.day, dt.hour, dt.minute)

def friendly_bytes(size):
    if size >= 1024*1024*1024:
        return '%dG' % (size // (1024*1024*1024))
    if size >= 1024*1024:
        return '%dM' % (size // (1024*1024))
    if size >= 1024:
        return '%dK' % (size // 1024)
    return size
