import logging

from ..util.intset import IntSet


_version_muls = {
    'm': 2**20,
    'k': 2**10}


def version_to_keywords(version):
    yield 'v:%d' % version
    for c, div in _version_muls.items():
        yield 'v:%d%s' % (version // div, c) 


def version_term_magic(term, max_version):
    try:
        word = term.split(':', 1)[1].lower()
        if '..' in word:
            beg, end = word.split('..')
            if (not beg) and end in ('current', ''):
                return IntSet.All
            if not end or end == 'current':
                end = max_version
            if not beg:
                beg = 0
            elif beg[-1:] == '+':
                beg = int(beg[:-1]) + 1
        elif word[-1:] == '+':
            beg = int(word[:-1]) + 1
            end = max_version
        elif word == 'recent':
            end = max_version
            beg = end - 200
        else:
            beg = end = word

        def _intify(ver):
            if isinstance(ver, int):
                return ver
            elif ver in _version_muls:
                return _version_muls[ver]
            elif ver[-1:] in _version_muls:
                return _version_muls[ver[-1:]] * int(ver[:-1])
            else:
                return int(ver)

        terms = []
        beg = _intify(beg)
        end = _intify(end)
        if beg > end:
            raise ValueError('%s > %s (out of range)' % (beg, end))

        k = 2**10
        m = 2**20
        beg_k = k * (beg // k + 1)
        beg_m = m * (beg // m + 1)
        end_k = k * (end // k)
        end_m = m * (end // m)

        # Add individual versions until we hit our first 1k boundary
        while (beg % k) and (beg % m) and (beg < beg_k) and (beg < end_k):
            terms.append('v:%d' % beg)
            beg += 1

        # Add chunks of 1k versions until we hit our first 1m boundary
        while (beg % m) and (beg+k <= beg_m) and (beg+k <= end_k):
            terms.append('v:%dk' % (beg // k))
            beg += k

        # Add chunks of 1m versions until we hit our final 1m boundary
        while (beg+m <= end+1):
            terms.append('v:%dm' % (beg // m))
            beg += m

        # Add chunks of 1k versions until we hit our final 1k boundary
        while (beg+k <= end+1):
            terms.append('v:%dk' % (beg // k))
            beg += k

        # Finally add individual versions until we are done
        while (beg <= end):
            terms.append('v:%d' % beg)
            beg += 1

        return tuple([IntSet.Or] + terms)
    except (ValueError, KeyError, IndexError, TypeError, NameError) as e:
        logging.debug('Failed to parse version %s: %s' % (term, e))
        return term


if __name__ == '__main__':
    from .engine import explain_ops

    import sys
    max_version = 100 * 2**20
    for term in sys.argv[1:]:
        if ':' not in term:
            max_version = int(term)
        else:
            print('Assuming max version is %d' % max_version)
            print(explain_ops(version_term_magic(term, max_version)))
            print()

    assert(3 == len(list(version_to_keywords(0))))
    assert('v:0' in version_to_keywords(0))

    def _vtm(term, _max=10 * 2**10):
        r = version_term_magic(term, _max)
        #print('Got: %s' % (r[1:] if isinstance(r, tuple) else r,))
        return r

    k = 2**10
    m = 2**20
    assert(_vtm('version:0..', k)[1:] == ('v:0k', 'v:1024'))

    assert(_vtm('version:..') == IntSet.All)
    assert(_vtm('version:8191+')[1:] == ('v:8k', 'v:9k', 'v:10240'))

    assert(_vtm('version:1k..') == _vtm('version:1k..current'))

    assert(explain_ops(_vtm('version:2012')) == '(v:2012)')
    assert(explain_ops(_vtm('version:2012..2014'))
        == '(v:2012 OR v:2013 OR v:2014)')

    assert(explain_ops(_vtm('version:1023..2048'))
        == ('(v:1023 OR v:1k OR v:2048)'))

    print('Tests pass OK')
