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
"""
import re

from ..util.intset import IntSet


def greedy_parse_terms(terms):
    terms = re.sub('["\'\s]+' , ' ',
        terms.replace('(', ' ( ').replace(')', ' ) ')
             .replace(' +', ' + ').replace('+ ', ' + ')
             .replace(' -', ' - ').replace('- ', ' - ').strip()
        ).split()

    def _flat(search):
        if len(search) == 2:
            return search[1]
        else:
            return tuple(search)

    search_stack = [[IntSet.And]]
    for term in terms:
        if term == '(':
            search_stack.append([IntSet.And])

        elif term == ')':
            if len(search_stack) > 1:
                done = search_stack.pop(-1)
                search_stack[-1].append(tuple(done))

        elif term in ('*', 'ALL'):
             search_stack[-1].append(IntSet.All)

        elif term in ('AND',):
            if search_stack[-1][0] != IntSet.And:
                search_stack[-1] = [IntSet.And, _flat(search_stack[-1])]

        elif term in ('+', 'OR'):
            if search_stack[-1][0] != IntSet.Or:
                search_stack[-1] = [IntSet.Or, _flat(search_stack[-1])]

        elif term in ('-', 'NOT'):
            if search_stack[-1][0] != IntSet.Sub:
                search_stack[-1] = [IntSet.Sub, _flat(search_stack[-1])]

        else:
             search_stack[-1].append(term.lower())

    # Close all dangling parens
    while len(search_stack) > 1:
        done = search_stack.pop(-1)
        search_stack[-1].append(tuple(done))

    return _flat(search_stack[-1])


if __name__ == '__main__':
    assert(greedy_parse_terms('yes hello world')
        == (IntSet.And, 'yes', 'hello', 'world'))

    assert(greedy_parse_terms('And AND hello +world +iceland')
        == (IntSet.Or, (IntSet.And, 'and', 'hello'), 'world', 'iceland'))

    assert(greedy_parse_terms('hello +world -iceland')
        == (IntSet.Sub, (IntSet.Or, 'hello', 'world'), 'iceland'))

    assert(greedy_parse_terms('hello +(world NOT iceland)')
        == (IntSet.Or, 'hello', (IntSet.Sub, 'world', 'iceland')))

    assert(greedy_parse_terms('hello + (world iceland)')
        == (IntSet.Or, 'hello', (IntSet.And, 'world', 'iceland')))

    assert(greedy_parse_terms('hello) OR (world iceland')
        == (IntSet.Or, 'hello', (IntSet.And, 'world', 'iceland')))

    assert(greedy_parse_terms('ALL - iceland')
        == (IntSet.Sub, IntSet.All, 'iceland'))

    print('Tests passed OK')
    import sys
    if sys.argv[1:]:
        print('%s' % (greedy_parse_terms(' '.join(sys.argv[1:])),))
