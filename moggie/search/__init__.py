import os
import struct

from ..util.dumbcode import dumb_decode, dumb_encode_bin
from ..util.intset import IntSet
from ..storage.records import RecordFile, RecordStore


class PostingListBucket:
    """
    A PostingListBucket is an unsorted sequence of binary packed
    (keyword, IntSet) pairs.
    """
    def __init__(self, blob, compress=None):
        self.blob = blob
        self.compress = compress

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
        iset |= ints
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
        'l1_min_len': 10,
        'l1_keywords': 512000,
        'l2_buckets': 4 * 1024 * 1024}

    def __init__(self, workdir,
            name='search', encryption_key=None, defaults=None):

        self.records = RecordStore(os.path.join(workdir, name), name,
            aes_key=encryption_key or b'',
            compress=64,
            sparse=True,
            est_rec_size=128,
            target_file_size=64*1024*1024)

        try:
            self.config = self.records[0]
        except (KeyError, IndexError):
            self.config = defaults or self.DEFAULTS
            self.records[0] = self.config

        try:
            self.part_space = self.records[1]
        except (KeyError, IndexError):
            self.part_space = (b' ' * (
                self.config['l1_min_len'] * self.config['l1_keywords']))

        self.l1_begin = 10
        self.l2_begin = self.l1_begin + self.config['l1_keywords']

    def delete_everything(self, *args):
        self.records.delete_everything(*args)

    def flush(self):
        return self.records.flush()

    def close(self):
        return self.records.close()

    def keyword_index(self, kw):
        kw_hash = self.records.hash_key(kw)

        # This duplicates logic from records.py, but we want to avoid
        # hashing the key twice.
        kw_pos_idx = self.records.keys.get(kw_hash)
        if kw_pos_idx is not None:
            return kw_pos_idx[1]

        kw_hash_int = struct.unpack('I', kw_hash[:4])[0] % self.config['l2_buckets']
        return kw_hash_int + self.l2_begin

    def add_results(self, results):
        keywords = {}
        for (r_id, kw_list) in results:
            if not isinstance(r_id, int):
                raise ValueError('Results must be integers')
            for kw in kw_list:
                keywords[kw] = keywords.get(kw, []) + [r_id]

        kw_idx_list = [(self.keyword_index(kw), kw) for kw in keywords]
        for idx, kw in sorted(kw_idx_list):
            if idx < self.l2_begin:
                # These are instances of IntSet, de/serialization is done
                # automatically by dumbcode.
                self.records[idx] |= keywords[kw]
            else:
                # These are instances of PostingList
                plb = PostingListBucket(self.records.get(idx) or b'')
                plb.add(kw, *keywords[kw])
                self.records[idx] = plb.blob

    def __getitem__(self, keyword):
        idx = self.keyword_index(keyword)
        if idx < self.l2_begin:
            raise KeyError('FIXME: Unimplemented')
        else:
            plb = PostingListBucket(self.records.get(idx) or b'')
            return plb.get(keyword) or IntSet()

    def search(self, term, _recursing=False):
        """
        Search for term in the index, returning an IntSet.

        If term is a tuple, it will OR together results for all terms.
        If term is a list, it will AND together results for all terms.

        These rules are recursively applied to the elements of the sets and
        tiples, allowing arbitrarily complex trees of AND/OR searches.
        """
        if isinstance(term, str):
            return self[term]

        if isinstance(term, list):
            return IntSet.And(*[self.search(t, _recursing=True) for t in term])

        if isinstance(term, tuple):
            return IntSet.Or(*[self.search(t, _recursing=True) for t in term])

        raise ValueError('Unknown supported search type: %s' % type(term))


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

    se = SearchEngine('/tmp', name='se-test')
    se.add_results([
        (1, ['hello', 'world', 'hooray']),
        (2, ['hello', 'iceland'])])

    # Basic search correctnesss
    assert(1 in se.search(['hello', 'world']))
    assert(2 not in se.search(['hello', 'world']))
    assert([] == list(se.search('notfound')))

    # Test our and/or functionality
    assert(list(se.search('hello')) == list(se.search(('world', 'iceland'))))

    print('Tests pass OK')
    import time
    time.sleep(10)
    se.delete_everything(True, False, True)
