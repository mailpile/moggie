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


def greedy_parse_terms(terms, magic_map={}):
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
                search_stack[-1].append(tuple(done))

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
                search_stack[-1].append(term.lower())
            else:
                search_stack[-1].append(_flat(term))
            changed = False

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

    def swapper_one(kw):
        return ':'.join(reversed(kw.split(':')))

    def swapper_many(kw):
        return (IntSet.Or, kw, ':'.join(reversed(kw.split(':'))))

    assert(greedy_parse_terms('yes hel:lo world', [
            (':', swapper_one),    # Maps to lo:hel
            (':', swapper_many)])  # ORs with hel:lo
        == (IntSet.And, 'yes', (IntSet.Or, 'lo:hel', 'hel:lo'), 'world'))

    print('Tests passed OK')
    import sys
    if sys.argv[1:]:
        print('%s' % (greedy_parse_terms(' '.join(sys.argv[1:])),))
