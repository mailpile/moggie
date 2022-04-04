import copy
import os
import struct
import re
import threading

from ..util.dumbcode import dumb_decode, dumb_encode_bin
from ..util.intset import IntSet
from ..util.wordblob import wordblob_search, create_wordblob, update_wordblob
from ..storage.records import RecordFile, RecordStore


def explain_ops(ops):
    if isinstance(ops, str):
        return ops
    if ops == IntSet.All:
        return 'ALL'

    if ops[0] == IntSet.Or:
        op = ' OR '
    elif ops[0] == IntSet.And:
        op = ' AND '
    elif ops[0] == IntSet.Sub:
        op = ' NOT '
    else:
        raise ValueError('What op is %s' % ops[0])
    return '('+ op.join([explain_ops(term) for term in ops[1:]]) +')'


class PostingListBucket:
    """
    A PostingListBucket is an unsorted sequence of binary packed
    (keyword, IntSet) pairs.
    """
    def __init__(self, blob, deleted=None, compress=None):
        self.blob = blob
        self.compress = compress
        self.deleted = deleted

    def __iter__(self):
        beg = 0
        while beg < len(self.blob):
            kw_len, iset_len = struct.unpack('II', self.blob[beg:beg+8])
            end = beg + 8 + kw_len + iset_len
            kw = self.blob[beg+8:beg+8+kw_len]
            beg = end
            yield kw

    def _find_iset(self, kw):
        bkeyword = kw if isinstance(kw, bytes) else bytes(kw, 'utf-8')

        beg = 0
        iset = None
        chunks = []
        while beg < len(self.blob):
            kw_len, iset_len = struct.unpack('II', self.blob[beg:beg+8])
            end = beg + 8 + kw_len + iset_len

            kw = self.blob[beg+8:beg+8+kw_len]
            if kw != bkeyword:
                chunks.append(self.blob[beg:end])
            else:
                iset_blob = self.blob[beg+8+kw_len:end]
                iset = dumb_decode(iset_blob)

            beg = end

        return chunks, bkeyword, iset

    def remove(self, keyword):
        chunks, bkeyword, iset = self._find_iset(keyword)
        if iset is not None:
            self.blob = b''.join(chunks)

    def add(self, keyword, *ints):
        chunks, bkeyword, iset = self._find_iset(keyword)

        if iset is None:
            iset = IntSet()
        if ints:
            iset |= ints
        if self.deleted is not None:
            iset -= self.deleted
        iset_blob = dumb_encode_bin(iset, compress=self.compress)

        chunks.append(struct.pack('II', len(bkeyword), len(iset_blob)))
        chunks.append(bkeyword)
        chunks.append(iset_blob)

        self.blob = b''.join(chunks)

    def get(self, keyword):
        chunks, bkeyword, iset = self._find_iset(keyword)
        return iset


class SearchEngine:
    """
    This is a keyword based search engine, which maps keywords to integers.

    Note: Performance depends on integers being relatively small (allocated
    sequentially from zero, hundreds of thousands to a few million items -
    larger valuse than that will require a redesign of our IntSet. We can
    cross that bridge when we come to it.
    """
    DEFAULTS = {
        'partial_list_len': 500000,  # Will expand to up ~5MB of storage
        'partial_min_hits': 3,
        'partial_shortest': 6,
        'partial_longest': 32,
        'partial_matches': 15,
        'l1_keywords': 512000,
        'l2_buckets': 4 * 1024 * 1024}

    IDX_CONFIG = 0
    IDX_PART_SPACE = 1
    IDX_MAX_RESERVED = 100

    IGNORE_SPECIAL_KW_RE = re.compile('(^\d+|[:@%"\'<>?!\._-]+)')
    IGNORE_NONLATIN_RE = re.compile('(^\d+|[:@%"\'<>?!\._-]+|'
        + '[^\u0000-\u007F\u0080-\u00FF\u0100-\u017F\u0180-\u024F])')

    def __init__(self, workdir,
            name='search', encryption_keys=None, defaults=None, maxint=0):

        self.records = RecordStore(os.path.join(workdir, name), name,
            salt=None, # FIXME: This must be set, OR ELSE
            aes_keys=encryption_keys or None,
            compress=64,
            sparse=True,
            est_rec_size=128,
            target_file_size=64*1024*1024)

        self.config = copy.copy(self.DEFAULTS)
        if defaults:
            self.config.update(defaults)
        try:
            self.config.update(self.records[self.IDX_CONFIG])
        except (KeyError, IndexError):
            self.records[self.IDX_CONFIG] = self.config
        print('engine config: %s' % (self.config,))

        try:
            self.part_spaces = [self.records[self.IDX_PART_SPACE]]
        except (KeyError, IndexError):
            self.part_spaces = [bytes()]

        self.l1_begin = self.IDX_MAX_RESERVED + 1
        self.l2_begin = self.l1_begin + self.config['l1_keywords']
        self.maxint = maxint
        self.deleted = IntSet()
        self.lock = threading.Lock()

        # Someday, these might be configurable/pluggable?

        from .parse_greedy import greedy_parse_terms
        self.parse_terms = greedy_parse_terms
        self.magic_map = [
            ('@', self.magic_emails),
            (':', self.magic_terms),
            ('*', self.magic_candidates)]

        from .dates import date_term_magic
        self.magic_term_map = {
            'date': date_term_magic,
            'dates': date_term_magic}

    def delete_everything(self, *args):
        with self.lock:
            self.records.delete_everything(*args)

    def flush(self):
        with self.lock:
            return self.records.flush()

    def close(self):
        with self.lock:
            return self.records.close()

    def iter_byte_keywords(self, min_hits=1, ignore_re=None):
        for i in range(self.l2_begin, len(self.records)):
            try:
                with self.lock:
                    plb = PostingListBucket(self.records[i])
                    for kw in plb:
                        if ignore_re:
                            if ignore_re.search(str(kw, 'utf-8')):
                                continue
                        if min_hits < 2:
                            yield kw
                            continue
                        count = 0
                        for i in plb.get(kw):
                            count += 1
                            if count >= min_hits:
                                yield kw
                                break
            except (IndexError, KeyError):
                pass

    def create_part_space(self, min_hits=0, ignore_re=IGNORE_NONLATIN_RE):
        self.part_spaces[0] = create_wordblob(self.iter_byte_keywords(
                min_hits=(min_hits or self.config['partial_min_hits']),
                ignore_re=ignore_re),
            shortest=self.config['partial_shortest'],
            longest=self.config['partial_longest'],
            maxlen=self.config['partial_list_len'],
            lru=True)
        self.records[self.IDX_PART_SPACE] = self.part_spaces[0]
        return self.part_spaces[0]

    def update_terms(self, terms, min_hits=0, ignore_re=IGNORE_NONLATIN_RE):
        adding = set()
        removing = set()
        ignoring = set()
        for kw in terms:
            kwb = bytes(kw, 'utf-8')
            if ignore_re:
                if ignore_re.search(kw):
                    ignoring.add(kwb)
                    continue
            if min_hits < 1:
                adding.add(kwb)
                continue
            count = 0
            for hit in self[term]:
                if count >= min_hits:
                    adding.add(kwb)
                    break
            if count < min_hits:
                removing.add(kwb)

        if adding or removing:
            blacklist = (removing | ignoring)
            for blob, wset in self.part_spaces[1:]:
                blacklist |= wset
                adding -= wset

        if adding or removing:
            self.part_spaces[0] = update_wordblob(adding, self.part_spaces[0],
                blacklist=blacklist,
                shortest=self.config['partial_shortest'],
                longest=self.config['partial_longest'],
                maxlen=self.config['partial_list_len'],
                lru=True)
            # FIXME: This becomes expensive if update batches are small!
            self.records[self.IDX_PART_SPACE] = self.part_spaces[0]

            with open('/tmp/part-space.txt', 'wb') as fd:
                fd.write(self.part_spaces[0])

        return self.part_spaces[0]

    def add_static_terms(self, wordlist):
        shortest = self.config['partial_shortest']
        longest = self.config['partial_longest']
        words = set([
            (bytes(w, 'utf-8') if isinstance(w, str) else w)
            for w in wordlist
            if shortest <= len(w) <= longest])
        self.part_spaces.append((create_wordblob(words,
                shortest=shortest,
                longest=longest,
                maxlen=len(words)+1),
            words))

    def add_dictionary_terms(self, dict_path):
        with open(dict_path, 'rb') as fd:
            blob = str(fd.read(), 'utf-8').lower()
            words = set([word.strip()
                for word in blob.splitlines()
                if not self.IGNORE_SPECIAL_KW_RE.search(word)])
        if words:
            self.add_static_terms(words)
        assert(b'hello' in self.part_spaces[-1][0])

    def candidates(self, keyword, max_results):
        blobs = [self.part_spaces[0]]
        blobs.extend(blob for blob, words in self.part_spaces[1:])
        clist = wordblob_search(keyword, blobs, max_results)
        return clist[:max_results]

    def _empty_l1_idx(self):
        for idx in range(self.l1_begin, self.l2_begin):
            if idx not in self.records:
                return idx
        raise None

    def keyword_index(self, kw, prefer_l1=False):
        kw_hash = self.records.hash_key(kw)

        # This duplicates logic from records.py, but we want to avoid
        # hashing the key twice.
        kw_pos_idx = self.records.keys.get(kw_hash)
        if kw_pos_idx is not None:
            return kw_pos_idx[1]
        elif prefer_l1:
            with self.lock:
                idx = self._empty_l1_idx()
                self.records.set_key(kw, idx)
                self.records[idx] = IntSet()
                return idx

        kw_hash_int = struct.unpack('I', kw_hash[:4])[0] % self.config['l2_buckets']
        return kw_hash_int + self.l2_begin

    def _prep_results(self, results, prefer_l1, tag_ns):
        keywords = {}
        hits = []
        extra_kws = ['in:'] if tag_ns else []
        for (r_ids, kw_list) in results:
            if isinstance(r_ids, int):
                r_ids = [r_ids]
            if isinstance(kw_list, str):
                kw_list = [kw_list]
            if not isinstance(r_ids, list):
                raise ValueError('Results must be (lists of) integers')
            for r_id in r_ids:
                if not isinstance(r_id, int):
                    raise ValueError('Results must be integers')
                if r_id > self.maxint:
                    self.maxint = r_id
                for kw in kw_list + extra_kws:
                    kw = kw.replace('*', '')  # Otherwise partial search breaks..
                    if tag_ns and kw[:3] == 'in:':
                        kw = '%s@%s' % (kw, tag_ns)
                    keywords[kw] = keywords.get(kw, []) + [r_id]
                if kw_list:
                    hits.append(r_id)

        kw_idx_list = [
            (self.keyword_index(kw, prefer_l1=prefer_l1), kw)
            for kw in keywords]

        return kw_idx_list, keywords, hits

    def _ns(self, k, ns):
        if ns and k[:3] == 'in:':
            return '%s@%s' % (k, ns)
        return k

    def rename_l1(self, kw, new_kw, tag_namespace=''):
        kw = self._ns(kw, tag_namespace)
        new_kw = self._ns(new_kw, tag_namespace)
        kw_pos_idx = self.records.keys[self.records.hash_key(kw)]
        self.records.set_key(new_kw, kw_pos_idx[1])
        self.records.del_key(kw)

    def mutate(self, mset, op_kw_list, tag_namespace=''):
        op_idx_kw_list = [
            (op, self.keyword_index(self._ns(kw, tag_namespace)))
            for op, kw in op_kw_list]

        for op, idx in op_idx_kw_list:
            if idx >= self.l2_begin:
                raise KeyError('Mutations not supported in l2')

        with self.lock:
            for op, idx in op_idx_kw_list:
                iset = self.records[idx]
                self.records[idx] = op(iset, mset)

        return {'mutations': len(op_idx_kw_list)}

    def del_results(self, results, tag_namespace=''):
        (kw_idx_list, keywords, hits
            ) = self._prep_results(results, False, tag_namespace)
        for idx, kw in sorted(kw_idx_list):
            with self.lock:
                if idx < self.l2_begin:
                    # These are instances of IntSet, de/serialization is done
                    # automatically by dumbcode.
                    iset = self.records[idx]
                    iset -= keywords[kw]
                    iset -= self.deleted
                    self.records[idx] = iset
                else:
                    # These are instances of PostingList
                    plb = PostingListBucket(self.records.get(idx) or b'')
                    plb.deleted = IntSet(copy=self.deleted)
                    plb.deleted |= keywords[kw]
                    plb.add(kw)
                    self.records[idx] = plb.blob
        self.update_terms(keywords)
        return {'keywords': len(keywords), 'hits': hits}

    def add_results(self, results, prefer_l1=False, tag_namespace=''):
        (kw_idx_list, keywords, hits
            ) = self._prep_results(results, prefer_l1, tag_namespace)
        for idx, kw in sorted(kw_idx_list):
            with self.lock:
                if idx < self.l2_begin:
                    # These are instances of IntSet, de/serialization is done
                    # automatically by dumbcode.
                    iset = self.records[idx]
                    iset |= keywords[kw]
                    iset -= self.deleted
                    self.records[idx] = iset
                else:
                    # These are instances of PostingList
                    plb = PostingListBucket(self.records.get(idx) or b'')
                    plb.deleted = self.deleted
                    plb.add(kw, *keywords[kw])
                    self.records[idx] = plb.blob
        self.update_terms(keywords)
        return {'keywords': len(keywords), 'hits': hits}

    def __getitem__(self, keyword):
        idx = self.keyword_index(keyword)
        if idx < self.l2_begin:
            return self.records.get(idx) or IntSet()
        else:
            plb = PostingListBucket(self.records.get(idx) or b'')
            return plb.get(keyword) or IntSet()

    def _search(self, term, tag_ns):
        if isinstance(term, tuple):
            op = term[0]
            return op(*[self._search(t, tag_ns) for t in term[1:]])

        if isinstance(term, str):
            if tag_ns and (term[:3] == 'in:'):
               return self['%s@%s' % (term, tag_ns)]
            elif term in ('in:', 'all:mail'):
               term = IntSet.All
            else:
               return self[term]

        if isinstance(term, list):
            return IntSet.And(*[self._search(t, tag_ns) for t in term])

        if term == IntSet.All:
            if tag_ns:
                return self['in:@%s' % tag_ns]
            return IntSet.All(self.maxint + 1)

        raise ValueError('Unknown supported search type: %s' % type(term))

    def explain(self, terms):
        return explain_ops(self.parse_terms(terms, self.magic_map))

    def search(self, terms, tag_namespace='', mask_deleted=True, explain=False):
        """
        Search for terms in the index, returning an IntSet.

        If term is a tuple, the first item must been an IntSet constructor
        (And, Or, Sub) which will be applied to the results for all terms,
        e.g. (IntSet.Sub, "hello", "world") to subtract all "world" matches
        from the "hello" results.

        These rules are recursively applied to the elements of the sets and
        tuples, allowing arbitrarily complex trees of AND/OR/SUB searches.
        """
        if isinstance(terms, str):
            ops = self.parse_terms(terms, self.magic_map)
        else:
            ops = terms
        if tag_namespace:
            # Explicitly search for "all:mail", to avoid returning results
            # from outside the namespace (which would otherwise happen when
            # searching without any tags at all).
            ops = (IntSet.And, IntSet.All, ops)
        with self.lock:
            rv = self._search(ops, tag_namespace)
            if mask_deleted:
                rv = IntSet.Sub(rv, self.deleted)
        if explain:
            rv = (tag_namespace, ops, rv)
        return rv

    def magic_terms(self, term):
        what = term.split(':')[0].lower()
        magic = self.magic_term_map.get(what)
        if magic is not None:
            return magic(term)

        # FIXME: Convert to:me, from:me into e-mail searches

        return term

    def magic_emails(self, term):
        return term  # FIXME: A no-op

    def magic_candidates(self, term):
        max_results = self.config.get('partial_matches', 10)
        matches = self.candidates(term, max_results)
        if len(matches) > 1:
            #print('Expanded %s(<%d) to %s' % (term, max_results, matches))
            return tuple([IntSet.Or] + matches)
        else:
            return matches[0]


if __name__ == '__main__':
  try:
    pl = PostingListBucket(b'', compress=128)
    pl.add('hello', 1, 2, 3, 4)
    assert(isinstance(pl.get('hello'), IntSet))
    assert(pl.get('floop') is None)
    assert(1 in pl.get('hello'))
    assert(5 not in pl.get('hello'))
    pl.add('hello', 5)
    assert(1 in pl.get('hello'))
    assert(5 in pl.get('hello'))
    pl.remove('hello')
    assert(pl.get('hello') is None)
    assert(len(pl.blob) == 0)

    # Create a mini search engine...
    def mk_se():
        k = b'1234123412349999'
        return SearchEngine('/tmp',
            name='se-test', encryption_keys=[k], defaults={
                'partial_list_len': 20,
                'partial_shortest': 4,
                'partial_longest': 14,  # excludes hellscapenation
                'l2_buckets': 10240})
    se = mk_se()
    se.add_results([
        (1, ['hello', 'hell', 'hellscapenation', 'hellyeah', 'world', 'hooray']),
        (3, ['please', 'remove', 'the', 'politeness']),
        (2, ['ell', 'hello', 'iceland', 'e*vil'])])
    se.add_results([
        (4, ['in:inbox', 'in:testing', 'in:bjarni'])],
        prefer_l1=True)
    se.add_results([
        (5, ['in:inbox', 'please'])],
        tag_namespace='work')

    se.deleted |= 0
    assert(list(se.search(IntSet.All)) == [1, 2, 3, 4, 5])

    assert(3 in se.search('please'))
    assert(5 in se.search('please'))
    assert(5 in se.search('please', tag_namespace='work'))
    assert(3 not in se.search('please', tag_namespace='work'))

    assert(3 in se.search('remove'))
    se.del_results([(3, ['please'])])
    assert(3 not in se.search('please'))
    assert(3 in se.search('remove'))

    assert(5 in se.search('in:inbox', tag_namespace='work'))
    assert(5 in se.search('all:mail', tag_namespace='work'))
    assert(4 not in se.search('all:mail', tag_namespace='work'))
    assert(3 not in se.search('in:inbox'))
    assert(4 in se.search('in:testing'))
    se.mutate(IntSet([4, 3]), [(IntSet.Sub, 'in:testing'), (IntSet.Or, 'in:inbox')])
    assert(4 not in se.search('in:testing'))
    assert(3 in se.search('in:inbox'))
    assert(4 in se.search('in:inbox'))
    assert(4 not in se.search('in:inbox', tag_namespace='work'))
    se.rename_l1('in:inbox', 'in:outbox')
    assert(4 in se.search('in:outbox'))
    assert(4 not in se.search('in:inbox'))
    try:
        se.mutate(IntSet([4, 3]), [(IntSet.Sub, 'hello'), (IntSet.Or, 'world')])
        assert(not 'reached')
    except KeyError:
        pass

    for round in range(0, 2):
        se.close()
        se = mk_se()

        # Basic search correctnesss
        assert(1 in se.search('hello world'))
        assert(2 not in se.search('hello world'))
        assert([] == list(se.search('notfound')))

        assert(4 in se.search('in:outbox'))
        assert(4 not in se.search('in:inbox'))

        # Enable and test partial word searches
        se.create_part_space(min_hits=1)
        assert(b'*' not in se.part_spaces[0])
        assert(b'evil' in se.part_spaces[0])  # Verify that * gets stripped
        #print('%s' % se.part_space)
        #print('%s' % se.candidates('*ell*', 10))
        assert(len(se.candidates('***', 10)) == 0)
        assert(len(se.candidates('ell*', 10)) == 1)   # ell
        assert(len(se.candidates('*ell', 10)) == 2)   # ell, hell
        #print(se.candidates('*ell*', 10))
        assert(len(se.candidates('*ell*', 10)) == 4)  # ell, hell, hello, hellyeah
        assert(len(se.candidates('he*ah', 10)) == 2)  # hepe, hellyeah
        assert(1 in se.search('hell* w*ld'))

        # Test our and/or functionality
        assert(list(se.search('hello')) == list(se.search((IntSet.Or, 'world', 'iceland'))))

        # Test the explainer and parse_terms with candidate magic
        assert(explain_ops(se.parse_terms('* - is:deleted he*o WORLD +Iceland', se.magic_map))
            == '(((ALL NOT is:deleted) AND (heo OR hello) AND world) OR iceland)')

        # Test the explainer and parse_terms with date range magic
        assert(se.explain('dates:2012..2013 OR date:2015')
            == '((year:2012 OR year:2013) OR year:2015)')

        # Test static and dictionary term expansions
        assert(se.candidates('orang*', 10) == ['orang'])
        se.add_static_terms(['red', 'green', 'blue', 'orange'])
        assert(se.candidates('orang*', 10) == ['orang', 'orange'])
        se.add_dictionary_terms('/usr/share/dict/words')
        assert('additional' in se.candidates('addit*', 20))

        se.records.compact()
        print('Tests pass OK (%d/2)' % (round+1,))

    #import time
    #time.sleep(10)
  finally:
    se.delete_everything(True, False, True)
