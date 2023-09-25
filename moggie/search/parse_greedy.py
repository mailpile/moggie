"""
This is a greedy query parser, which doesn't assume any priority
of operations at all, it's just "greedy", evaluating each term and
operation as it appears when read from left to right.

This will offend mathematicians, but should be relatively accessible
to other humans?

Our query language is very simple:

  term     .. This term is required
  + term   .. This term's results are added to the result set
  - term   .. This term's results are subtracted from the result set

Parenthesis can be used to group operations together, the all-caps words
AND, OR and NOT can be used in place of (nothing), + and - respectively.

The word ALL (or *) represents all results.

So "hello +world -iceland" would search for "hello" OR "world", but
remove any result matching "iceland".

However, "hello + (world - iceland)" would search for "hello" OR
anything matching "world" but not matching "iceland".

Note that the search engine itself will then handle globbing of
individual keywords, so searching for "hell* world" might become
equivalent to "(hell OR hello OR hellsite) AND world".

Small words (<3 letters) are considered to be part of both the preceding
and following words: "hello my world" becomes "hello my" AND "my world".
"""
import re

from ..util.intset import IntSet


def greedy_parse_terms(terms, magic_map={}):
    terms = re.sub('["\'\s]+' , ' ',
        terms.replace('(', ' ( ').replace(')', ' ) ')
             .replace(' +', ' + ').replace('+ ', ' + ')
             .replace(' -', ' - ').replace('- ', ' - ').strip()
        ).split()

    # FIXME: A smarter split function would be nice here, supporting
    #        quotes would be quite grown-up of us.

    def _flat(search):
        if search == IntSet.All:
            return search
        elif len(search) == 2:
            return search[1]
        else:
            return tuple(search)

    def _make_pairs(srch):
        op = srch[0]
        if (len(srch) < 3) or (op not in (IntSet.And, IntSet.Or)):
            return srch
        for i in reversed(range(1, len(srch) - 1)):
            if (isinstance(srch[i], str) and isinstance(srch[i+1], str)
                   and ('*' not in srch[i])
                  #and ('*' not in srch[i+1])
                   and (':' not in srch[i])
                   and (':' not in srch[i+1])
                   and (' ' not in srch[i+1])
                   and ((len(srch[i]) < 4) or (len(srch[i+1]) < 4))):
                if ((i == 1) or len(srch[i]) >= 4) and (op == IntSet.And):
                    srch[i:i+2] = ['%s %s' % (srch[i], srch[i+1])]
                else:
                    srch[i:i+2] = [srch[i], '%s %s' % (srch[i], srch[i+1])]
        return srch

    search_stack = [[IntSet.And]]
    changed = False
    for term in terms:
        if term == '(':
            changed = False
            search_stack.append([IntSet.And])

        elif term == ')':
            changed = False
            if len(search_stack) > 1:
                changed = True
                done = search_stack.pop(-1)
                search_stack[-1].append(tuple(_make_pairs(done)))

        elif term in ('*', 'ALL'):
            search_stack[-1].append(IntSet.All)
            changed = False

        elif term in ('AND',):
            changed = True
            if search_stack[-1][0] != IntSet.And:
                search_stack[-1] = [IntSet.And, _flat(search_stack[-1])]

        elif term in ('+', 'OR'):
            changed = True
            if search_stack[-1][0] != IntSet.Or:
                search_stack[-1] = [IntSet.Or, _flat(search_stack[-1])]

        elif term in ('-', 'NOT'):
            changed = True
            if search_stack[-1][0] != IntSet.Sub:
                search_stack[-1] = [IntSet.Sub, _flat(search_stack[-1])]

        else:
            if not changed and (search_stack[-1][0] != IntSet.And):
                # No operator by default equals AND, so if we didn't set an
                # operator last time, but the current isn't AND: fix it.
                search_stack[-1] = [IntSet.And, _flat(search_stack[-1])]

            for char, magic in magic_map:
                if char in term:
                    term = magic(term)
                    if not isinstance(term, str):
                        break

            if isinstance(term, str):
                if not term.startswith('id:'):
                    term = term.lower()
                search_stack[-1].append(term)
            else:
                search_stack[-1].append(_flat(term))
            changed = False

    # Close all dangling parens
    while len(search_stack) > 1:
        done = search_stack.pop(-1)
        search_stack[-1].append(tuple(_make_pairs(done)))

    return _flat(_make_pairs(search_stack[-1]))


if __name__ == '__main__':
    import sys
    if sys.argv[1:]:
        print('Parsed: %s' % (greedy_parse_terms(' '.join(sys.argv[1:])),))

    def _assert(val, want=True, msg='assert'):
        if isinstance(want, bool):
            if (not val) == (not want):
                want = val
        if val != want:
            raise AssertionError('%s(%s==%s)' % (msg, val, want))

    _assert(greedy_parse_terms('yes in:this-rocks.whee@foo world'),
        (IntSet.And, 'yes', 'in:this-rocks.whee@foo', 'world'))

    _assert(greedy_parse_terms('yes hello world'),
        (IntSet.And, 'yes hello', 'world'))

    _assert(greedy_parse_terms('And AND hello +world +iceland'),
        (IntSet.Or, (IntSet.And, 'and', 'hello'), 'world', 'iceland'))

    _assert(greedy_parse_terms('hello +world -iceland'),
        (IntSet.Sub, (IntSet.Or, 'hello', 'world'), 'iceland'))

    _assert(greedy_parse_terms('hello +(world NOT iceland)'),
        (IntSet.Or, 'hello', (IntSet.Sub, 'world', 'iceland')))

    _assert(greedy_parse_terms('hello + (world iceland)'),
        (IntSet.Or, 'hello', (IntSet.And, 'world', 'iceland')))

    _assert(greedy_parse_terms('hello) OR (world iceland'),
        (IntSet.Or, 'hello', (IntSet.And, 'world', 'iceland')))

    _assert(greedy_parse_terms('ALL - iceland'),
        (IntSet.Sub, IntSet.All, 'iceland'))

    def swapper_one(kw):
        return ':'.join(reversed(kw.split(':')))

    def swapper_many(kw):
        return (IntSet.Or, kw, ':'.join(reversed(kw.split(':'))))

    assert(greedy_parse_terms('yes hel:lo world', [
            (':', swapper_one),    # Maps to lo:hel
            (':', swapper_many)])  # ORs with hel:lo
        == (IntSet.And, 'yes', (IntSet.Or, 'lo:hel', 'hel:lo'), 'world'))

    print('Tests passed OK')
