import copy
import os
import struct
import threading

from ..util.dumbcode import dumb_decode, dumb_encode_bin
from ..util.intset import IntSet
from ..util.wordblob import wordblob_search, create_wordblob
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

    def _find_iset(self, keyword):
        bkeyword = bytes(keyword, 'utf-8')

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
        'partial_list_len': 128000,
        'partial_shortest': 5,
        'partial_longest': 32,
        'partial_matches': 10,
        'l1_keywords': 512000,
        'l2_buckets': 4 * 1024 * 1024}

    IDX_CONFIG = 0
    IDX_PART_SPACE = 1
    IDX_MAX_RESERVED = 100

    def __init__(self, workdir,
            name='search', encryption_key=None, defaults=None, maxint=0):

        self.records = RecordStore(os.path.join(workdir, name), name,
            aes_key=encryption_key or b'',
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

        try:
            self.part_space = self.records[self.IDX_PART_SPACE]
        except (KeyError, IndexError):
            self.part_space = bytes()

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

    def iter_byte_keywords(self):
        for i in range(self.l2_begin, len(self.records)):
            try:
                with self.lock:
                    for kw in PostingListBucket(self.records[i]):
                        yield kw
            except (IndexError, KeyError):
                pass

    def create_part_space(self):
        self.part_space = create_wordblob(self.iter_byte_keywords(),
            shortest=self.config['partial_shortest'],
            longest=self.config['partial_longest'],
            maxlen=self.config['partial_list_len'])
        self.records[self.IDX_PART_SPACE] = self.part_space
        return self.part_space

    def candidates(self, keyword, max_results):
        return wordblob_search(keyword, self.part_space, max_results)

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

    def _prep_results(self, results, prefer_l1):
        keywords = {}
        hits = []
        for (r_id, kw_list) in results:
            if not isinstance(r_id, int):
                raise ValueError('Results must be integers')
            if r_id > self.maxint:
                self.maxint = r_id
            for kw in kw_list:
                kw = kw.replace('*', '')  # Otherwise partial search breaks..
                keywords[kw] = keywords.get(kw, []) + [r_id]
            if kw_list:
                hits.append(r_id)

        kw_idx_list = [
            (self.keyword_index(kw, prefer_l1=prefer_l1), kw)
            for kw in keywords]

        return kw_idx_list, keywords, hits

    def rename_l1(self, kw, new_kw):
        kw_pos_idx = self.records.keys[self.records.hash_key(kw)]
        self.records.set_key(new_kw, kw_pos_idx[1])
        self.records.del_key(kw)

    def mutate(self, mset, op_kw_list):
        op_idx_kw_list = [
            (op, self.keyword_index(kw), kw)
            for op, kw in op_kw_list]

        for op, idx, kw in op_idx_kw_list:
            if idx >= self.l2_begin:
                raise KeyError('Mutations not supported in l2')

        with self.lock:
            for op, idx, kw in op_idx_kw_list:
                iset = self.records[idx]
                self.records[idx] = op(iset, mset)

        return {'mutations': len(op_idx_kw_list)}

    def del_results(self, results):
        kw_idx_list, keywords, hits = self._prep_results(results, False)
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
        # FIXME: Update our wordblob
        return {'keywords': len(keywords), 'hits': hits}

    def add_results(self, results, prefer_l1=False):
        kw_idx_list, keywords, hits = self._prep_results(results, prefer_l1)
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
        # FIXME: Update our wordblob
        return {'keywords': len(keywords), 'hits': hits}

    def __getitem__(self, keyword):
        idx = self.keyword_index(keyword)
        if idx < self.l2_begin:
            return self.records.get(idx) or IntSet()
        else:
            plb = PostingListBucket(self.records.get(idx) or b'')
            return plb.get(keyword) or IntSet()

    def _search(self, term):
        if isinstance(term, tuple):
            op = term[0]
            return op(*[self._search(t) for t in term[1:]], clone=True)

        if isinstance(term, str):
            return self[term]

        if isinstance(term, list):
            return IntSet.And(*[self._search(t) for t in term])

        if term == IntSet.All:
            return IntSet.All(self.maxint + 1)

        raise ValueError('Unknown supported search type: %s' % type(term))

    def explain(self, terms):
        return explain_ops(self.parse_terms(terms, self.magic_map))

    def search(self, terms, mask_deleted=True, explain=False):
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
        with self.lock:
            if mask_deleted:
                rv = IntSet.Sub(self._search(ops), self.deleted)
            else:
                rv = self._search(ops)
        if explain:
            rv = (ops, rv)
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
        matches = self.candidates(term, self.config.get('partial_matches', 10))
        if len(matches) > 1:
            return tuple([IntSet.Or] + matches)
        else:
            return matches[0]


if __name__ == '__main__':
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
        return SearchEngine('/tmp', name='se-test', encryption_key=k, defaults={
            'partial_list_len': 20,
            'partial_shortest': 4,
            'partial_longest': 8,  # Will exclude hellscape from partial set
            'l2_buckets': 10240})
    se = mk_se()
    se.add_results([
        (1, ['hello', 'hell', 'hellscape', 'hellyeah', 'world', 'hooray']),
        (3, ['please', 'remove', 'the', 'politeness']),
        (2, ['ell', 'hello', 'iceland', 'e*vil'])])
    se.add_results([
        (4, ['in:inbox', 'in:testing', 'in:bjarni'])],
        prefer_l1=True)

    se.deleted |= 0
    assert(list(se.search(IntSet.All)) == [1, 2, 3, 4])

    assert(3 in se.search('please'))
    assert(3 in se.search('remove'))
    se.del_results([(3, ['please'])])
    assert(3 not in se.search('please'))
    assert(3 in se.search('remove'))

    assert(3 not in se.search('in:inbox'))
    assert(4 in se.search('in:testing'))
    se.mutate(IntSet([4, 3]), [(IntSet.Sub, 'in:testing'), (IntSet.Or, 'in:inbox')])
    assert(4 not in se.search('in:testing'))
    assert(3 in se.search('in:inbox'))
    assert(4 in se.search('in:inbox'))
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
        se.create_part_space()
        assert(b'*' not in se.part_space)
        assert(b'evil' in se.part_space)  # Verify that * gets stripped
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

        se.records.compact()
        print('Tests pass OK (%d/2)' % (round+1,))

    import time
    time.sleep(10)
    se.delete_everything(True, False, True)
