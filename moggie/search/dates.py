import datetime
import logging
import time

from ..util.intset import IntSet


_date_offsets = {
    'today': 0,
    'yesterday': 1,
    'd': 1,
    'w': 7,
    'm': 31,
    'q': 92,
    'y': 366}  # Any year could be leap year...


# Note: We include January twice, to allow for wraparound.
#       For simplicity, assume any year could be a leap-year.
_month_len = [None, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31, 31]


def _adjust(d):
    mlen = _month_len[d[1]]
    if d[2] > mlen:
        d[2] -= mlen
        d[1] += 1
    if d[1] > 12:
        d[0] += 1
        d[1] -= 12


def _mk_date(ts):
    mdate = datetime.date.fromtimestamp(ts)
    return '%d-%d-%d' % (mdate.year, mdate.month, mdate.day)


def ts_to_keywords(msg_ts, kw_date=None):
    mdate = datetime.date.fromtimestamp(msg_ts)
    if kw_date:
        return [
            '%s:%s'       % (kw_date, mdate.year),
            '%s:%s-%s'    % (kw_date, mdate.year, mdate.month),
            '%s:%s-%s-%s' % (kw_date, mdate.year, mdate.month, mdate.day)]

    return [
        'day:%s'        % mdate.day,
        'month:%s'      % mdate.month,
        'year:%s'       % mdate.year,
        'date:%s-%s'    % (mdate.year, mdate.month),
        'date:%s-%s-%s' % (mdate.year, mdate.month, mdate.day)]


def date_term_magic(term, kw_date=None):
    try:
        if kw_date:
            kw_year = kw_date
        else:
            kw_year = 'year'
            kw_date = 'date'

        word = term.split(':', 1)[1].lower()
        if word == 'recent':
            word = '13d..today'  # FIXME: Is 2 weeks recent?
        if '..' in word:
            start, end = word.split('..')
            if (not start) and end in ('today', '0d', '0w', '0m', '0q', '0y', ''):
                return IntSet.All
            if not end:
                end = 'today'
            if not start:
                start = '20y'  # FIXME: This is incorrect
        else:
            start = end = word

        if end in _date_offsets:
            end = _mk_date(time.time() - _date_offsets[end]*24*3600)
        elif end[-1:] in _date_offsets:
            do = _date_offsets[end[-1:]]
            end = _mk_date(time.time() - int(end[:-1])*do*24*3600)
        elif len(end) >= 9 and '-' not in end:
            end = _mk_date(int(end))

        if start in _date_offsets:
            start = _mk_date(time.time() - _date_offsets[start]*24*3600)
        elif start[-1:] in _date_offsets:
            do = _date_offsets[start[-1:]]
            start = _mk_date(time.time() - int(start[:-1])*do*24*3600)
        elif len(start) >= 9 and '-' not in start:
            start = _mk_date(int(start))

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
                    terms.append('%s:%d' % (kw_year, start[0]))
                    start[0] += 1
                    continue

            # Move forward one month?
            if start[2] == 1:
                nm = [start[0], start[1], 31]
                if nm <= end:
                    terms.append('%s:%d-%d' % (kw_date, start[0], start[1]))
                    start[1] += 1
                    _adjust(start)
                    continue

            # Move forward one day...
            terms.append('%s:%d-%d-%d' % tuple([kw_date] + start))
            start[2] += 1
            _adjust(start)

        return tuple([IntSet.Or] + terms)
    except (ValueError, KeyError, IndexError, TypeError, NameError):
        logging.exception('Failed to parse date: %s' % term)
        return term


if __name__ == '__main__':
    from .engine import explain_ops

    import sys
    for term in sys.argv[1:]:
        print(explain_ops(date_term_magic(term)))

    assert(5 == len(ts_to_keywords(0)))
    assert('date:1970-1-1' in ts_to_keywords(0))

    assert(3 == len(ts_to_keywords(0, kw_date='anno')))
    assert('anno:1970-1-1' in ts_to_keywords(0, kw_date='anno'))

    assert(len(date_term_magic('dates:7d..')) == 8+1)
    assert(len(date_term_magic('dates:recent')) == 14+1)

    assert(date_term_magic('dates:..') == IntSet.All)
    assert(date_term_magic('dates:..0y') == IntSet.All)
    assert(date_term_magic('dates:..today') == IntSet.All)

    assert(date_term_magic('dates:3d..') == date_term_magic('dates:3d..today'))

    assert(explain_ops(date_term_magic('dates:2012'))
        == '(year:2012)')

    assert(explain_ops(date_term_magic('dates:2012', kw_date='anno'))
        == '(anno:2012)')

    assert(explain_ops(date_term_magic('dates:2012-10', kw_date='dags'))
        == '(dags:2012-10)')

    assert(explain_ops(date_term_magic('dates:2012..2014'))
        == '(year:2012 OR year:2013 OR year:2014)')

    assert(explain_ops(date_term_magic('dates:2021-10-30..2021-12'))
        == ('(date:2021-10-30 OR date:2021-10-31 OR '
            'date:2021-11 OR date:2021-12)'))

    assert(explain_ops(date_term_magic('dates:2021-02-27..2021-03'))
        == ('(date:2021-2-27 OR date:2021-2-28 OR date:2021-2-29'
            ' OR date:2021-3)'))

    print('Tests pass OK')
