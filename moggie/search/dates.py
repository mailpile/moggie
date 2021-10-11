import time
import datetime

from ..util.intset import IntSet


def _adjust(d):
    if d[2] > 31:
        d[1] += 1
        d[2] -= 31
    if d[1] > 12:
        d[0] += 1
        d[1] -= 12


def _mk_date(ts):
    mdate = datetime.date.fromtimestamp(ts)
    return '%d-%d-%d' % (mdate.year, mdate.month, mdate.day)


_date_offsets = {
    'today': 0,
    'yesterday': 1,
    'd': 1,
    'w': 7,
    'm': 31,
    'q': 91}



def ts_to_keywords(msg_ts):
    mdate = datetime.date.fromtimestamp(msg_ts)
    return [
        'year:%s' % mdate.year,
        'month:%s' % mdate.month,
        'day:%s' % mdate.day,
        'yearmonth:%s-%s' % (mdate.year, mdate.month),
        'date:%s-%s-%s' % (mdate.year, mdate.month, mdate.day)]


def date_term_magic(term):
    try:
        word = term.split(':', 1)[1].lower()
        if '..' in term:
            start, end = word.split('..')
        else:
            start = end = word

        if end in _date_offsets:
            end = _mk_date(time.time() - _date_offsets[end]*24*3600)
        elif end[-1:] in _date_offsets:
            do = _date_offsets[end[-1:]]
            end = _mk_date(time.time() - int(end[:-1])*do*24*3600)
        elif len(end) >= 9 and '-' not in end:
            end = _mk_date(long(end))

        if start in _date_offsets:
            start = _mk_date(time.time() - _date_offsets[start]*24*3600)
        elif start[-1:] in _date_offsets:
            do = _date_offsets[start[-1:]]
            start = _mk_date(time.time() - int(start[:-1])*do*24*3600)
        elif len(start) >= 9 and '-' not in start:
            start = _mk_date(long(start))

        start = [int(p) for p in start.split('-')][:3]
        end = [int(p) for p in end.split('-')[:3]]
        while len(start) < 3:
            start.append(1)
        if len(end) == 1:
            end.extend([12, 31])
        elif len(end) == 2:
            end.append(31)
        if not start <= end:
            raise ValueError()

        terms = []
        while start <= end:
            # Move forward one year?
            if start[1:] == [1, 1]:
                ny = [start[0], 12, 31]
                if ny <= end:
                    terms.append('year:%d' % start[0])
                    start[0] += 1
                    continue

            # Move forward one month?
            if start[2] == 1:
                nm = [start[0], start[1], 31]
                if nm <= end:
                    terms.append('yearmonth:%d-%d' % (start[0], start[1]))
                    start[1] += 1
                    _adjust(start)
                    continue

            # Move forward one day...
            terms.append('date:%d-%d-%d' % tuple(start))
            start[2] += 1
            _adjust(start)

        return tuple([IntSet.Or] + terms)
    except (ValueError, KeyError, IndexError, TypeError, NameError):
        return term


if __name__ == '__main__':
    from . import explain_ops

    assert(explain_ops(date_term_magic('dates:2012'))
        == '(year:2012)')

    assert(explain_ops(date_term_magic('dates:2012..2014'))
        == '(year:2012 OR year:2013 OR year:2014)')

    assert(explain_ops(date_term_magic('dates:2021-10-30..2021-12'))
        == ('(date:2021-10-30 OR date:2021-10-31 OR '
            'yearmonth:2021-11 OR yearmonth:2021-12)'))

    print('Tests pass OK')
