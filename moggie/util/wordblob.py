"""
These are very simple routines for simulating partial matches when
searching in a keyword-based index; we generate a single buffer in RAM
containing all the keywords, which can then be quickly searched for
substring matches.

These substring matches then give us candidate keywords to search for in
the main search index.

This should be particularly helpful when searching in languages that use
declinations/conjugations, but words have a common "root".

The search function supports MS-DOS style asterisks, where an asterisk
(*) matches zero or more characters.

(Since the searches are performed on a single buffer in RAM, using the
regular expression engine, it is actually possible to search for complex
regep patterns to generate keyword candiates. Whether this will prove
useful is unknown at this time, but it's a neat trick!)
"""
import re
import random


def wordblob_search(term, blob, max_results):
    """
    Search for <term> in <blob>, returning up the <max_results> matches,
    ordered by how exact the match is. The term itself, stripped of
    asterisks, is always the first match, even if it is not present in
    the blob itself.
    """
    keyword = term if isinstance(term, bytes) else bytes(term, 'utf-8')
    matches = [(0, keyword.replace(b'*', b''))]
    if not matches[0][1]:
        return []

    bind_beg = (keyword[:1] != b'*')
    bind_end = (keyword[-1:] != b'*')

    search_re = re.compile(keyword.strip(b'*').replace(b'*', b'[^\\n]*'))
    for m in re.finditer(search_re, blob):
        beg, end = m.span()

        # Note: Doing this here, rather than using complex regexp
        #       magic is *much* faster when our blobs get large.
        if bind_beg and (beg > 0) and (blob[beg-1:beg] != b'\n'):
            continue
        if bind_end and (end < len(blob)) and (blob[end:end+1] != b'\n'):
            continue

        # Expand our match to grab the full keyword from the blob.
        b1 = beg
        while (beg > 0) and (blob[beg-1:beg] != b'\n'):
            beg -= 1
        while (end < len(blob)) and (blob[end:end+1] != b'\n'):
            end += 1

        # Append our match, calculating a rough weight based on how
        # close it is to being an exact match.
        kw = blob[beg:end]
        if kw not in (matches[0][1], matches[-1][1]):
            matches.append((10 * len(kw) // len(keyword) + b1, kw))
            if len(matches) > max_results*10:
                break

    return [str(kw, 'utf-8') for s, kw in sorted(matches)[:max_results]]


def create_wordblob(iter_keywords, shortest=4, longest=40, maxlen=102400):
    """
    Generate a blob of keywords, applying the given criteria, for use
    with the wordblob_search() function. The <iter_keywords> should be
    a list or iterable of keywords encoded as bytes().
    """
    keywords = set([])
    for kw in iter_keywords:
        if (shortest <= len(kw) <= longest) and (b'*' not in kw):
            keywords.add(kw)

    # Stay within our length limits; current strategy is to randomly
    # drop long words until we fit. Is that sane? FIXME?
    keywords = list(keywords)
    while len(keywords) > maxlen:
        longish = [kw for kw in keywords if len(kw) == longest]
        keywords = [kw for kw in keywords if len(kw) < longest]
        more = maxlen - len(keywords)
        if more > 0:
            keywords.extend(random.sample(longish, more))
            break
        longest -= 1

    return b'\n'.join(sorted(keywords))


if __name__ == '__main__':
    import time

    blob = create_wordblob([bytes(w, 'utf-8') for w in [
            'hello', 'world', 'this', 'is', 'great', 'oh', 'yeah',
            'thislongwordgetsignored'
        ]],
        shortest=2,
        longest=5,
        maxlen=20)

    # The noop is to just return the keyword itself!
    assert(wordblob_search('bjarni', b'', 10) == ['bjarni'])
    assert(wordblob_search('bja*rni', b'', 10) == ['bjarni'])

    # Searches...
    assert(wordblob_search('*', blob, 10) == [])
    assert(wordblob_search('*****', blob, 10) == [])
    assert(wordblob_search('worl*', blob, 10) == ['worl', 'world'])
    assert(wordblob_search('*orld', blob, 10) == ['orld', 'world'])
    assert(wordblob_search('*at', blob, 10) == ['at', 'great'])
    assert(wordblob_search('w*d', blob, 10) == ['wd', 'world'])
    assert(wordblob_search('*w*r*d*', blob, 10) == ['wrd', 'world'])

    blob2 = create_wordblob((
            b'%d' % random.randint(10000, 10240000) for i in range(0, 130000)
        ),
        shortest=5,
        maxlen=128000)
    assert(len(blob2.split()) == 128000)

    #blob2 = b'\n'.join([blob2, blob2, blob2, blob2])
    #print('%s' % wordblob_search('10[12345]+', blob2, 10))

    n = 250
    t0 = time.time()
    for i in range(0, n):
        wordblob_search('%d*' % random.randint(0, 10240), blob2, 10)
    t1 = time.time()
    for i in range(0, n):
        wordblob_search('%d*0' % random.randint(0, 10240), blob2, 10)
    t2 = time.time()
    for i in range(0, n):
        wordblob_search('*%d' % random.randint(0, 10240), blob2, 10)
    t3 = time.time()
    for i in range(0, n):
        wordblob_search('*%d*' % random.randint(0, 10240), blob2, 10)
    t4 = time.time()

    s1 = n / (t1-t0)
    s2 = n / (t2-t1)
    s3 = n / (t3-t2)
    s4 = n / (t4-t3)

    print('Tests pass OK: %d/%d/%d/%d qps in %d byte blob' % (s1, s2, s3, s4, len(blob2)))
