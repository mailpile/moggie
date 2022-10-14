# Other thoughts:
#   - what do we need to start providing search suggestions?
#   - what do we need to start providing autosuggested canned replies??
#   - we want to search e-mail addresses
#   - we need undo for tag operations
#   - undo implies a search history, implies broadcasting change notifications
#   - the modification history of a message... how?
#       - in the metadata: does not scale to large mutations
#       - tagged:2022-08-01 ?     date?    == 366 keywords per year
#       - tagged:2022-08-01-12 ?  date+hr? == 8784 keywords per year
#       - tagged:12-30 ?       minute?  == 1440 keywords, assuming cleanup
#       - tagged-inbox:2022-08-01       == 366 keywords per year per tag
#       - How quickly can we scan all the mutation keywords?
#           ... we could perform cleanups, merge tagged:2022-08-01-* into
#           ... lets us implement changed:* magic
#
# I don't think we need search history, since entire result sets are compact
# enough for us to pass them around when needed.
#
# However, we do want tag-op history, since those are pretty complex. A tagging
# operation will be an ordered set of mutations; to undo the mutations need to
# be reversed in reverse order. A tagging operation should also be annotated
# with a human-readable description of what was done, and that should be
# provided by the user-interface, since the engine cannot really know what UX
# action the tagging operation represents.
#
# Another concern:
#   - Tagging a message into a tag_namespace should also add the tag to the
#     in:@ns "all mail".
#   - We need to better think through how importing and namespaces are going
#     to interact. Although the above might suffice in practice, since tagging
#     in:inbox@ns will do the job just fine?
#
import copy
import logging
import os
import struct
import random
import re
import threading
import time

from ..util.dumbcode import dumb_decode, dumb_encode_bin, dumb_encode_asc
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
    (keyword, comment, IntSet) tuples.
    """
    def __init__(self, blob, deleted=None, compress=None):
        self.blob = blob
        self.compress = compress
        self.deleted = deleted

    def __iter__(self):
        beg = 0
        while beg < len(self.blob):
            kw_ln, c_ln, iset_ln = struct.unpack('<HHI', self.blob[beg:beg+8])
            yield self.blob[beg+8:beg+8+kw_ln]
            beg += 8 + kw_ln + c_ln + iset_ln

    def items(self, decode=True):
        beg = 0
        decode = dumb_decode if decode else (lambda b: b)
        while beg < len(self.blob):
            kw_ln, c_ln, iset_ln = struct.unpack('<HHI', self.blob[beg:beg+8])
            cbeg = beg + 8 + kw_ln
            end = cbeg + c_ln + iset_ln

            kw = self.blob[beg+8:cbeg]
            bcomment = self.blob[cbeg:cbeg+c_ln]
            iset_blob = self.blob[cbeg+c_ln:end]
            yield (kw, bcomment, decode(iset_blob))

            beg = end

    def _find_iset(self, kw):
        bkeyword = kw if isinstance(kw, bytes) else bytes(kw, 'utf-8')

        beg = 0
        iset = None
        bcomment = b''
        chunks = []
        while beg < len(self.blob):
            kw_ln, c_ln, iset_ln = struct.unpack('<HHI', self.blob[beg:beg+8])
            cbeg = beg + 8 + kw_ln
            end = cbeg + c_ln + iset_ln

            kw = self.blob[beg+8:cbeg]
            if kw != bkeyword:
                chunks.append(self.blob[beg:end])
            else:
                bcomment = self.blob[cbeg:cbeg+c_ln]
                iset_blob = self.blob[cbeg+c_ln:end]
                iset = dumb_decode(iset_blob)

            beg = end

        return chunks, bkeyword, bcomment, iset

    def remove(self, keyword):
        chunks, bkeyword, bcomment, iset = self._find_iset(keyword)
        if iset is not None:
            self.blob = b''.join(chunks)
        return bcomment, iset

    def add(self, keyword, ints, comment=b''):
        chunks, bkeyword, bcomment, iset = self._find_iset(keyword)

        if iset is None:
            iset = IntSet()
        if ints:
            iset |= ints
        if self.deleted is not None:
            iset -= self.deleted

        self.set(keyword, iset, bcomment, bkeyword, chunks)

    def set_comment(self, keyword, comment):
        bcomment = comment
        if not isinstance(bcomment, bytes):
            bcomment = bytes(bcomment, 'utf-8')
        chunks, bkeyword, ocomment, iset = self._find_iset(keyword)
        self.set(keyword, iset, bcomment, bkeyword, chunks)

    def set(self, keyword, iset, comment=b'', bkeyword=None, chunks=None):
        bcomment = b''
        if not (bkeyword and chunks):
            chunks, bkeyword, bcomment, _ = self._find_iset(keyword)

        bcomment = comment or bcomment or b''
        if not isinstance(bcomment, bytes):
            bcomment = bytes(bcomment, 'utf-8')

        iset_blob = dumb_encode_bin(iset, compress=self.compress)
        chunks.append(
            struct.pack('<HHI', len(bkeyword), len(bcomment), len(iset_blob)))
        chunks.append(bkeyword)
        chunks.append(bcomment)
        chunks.append(iset_blob)
        self.blob = b''.join(chunks)

    def get(self, keyword, with_comment=False):
        chunks, bkeyword, bcomment, iset = self._find_iset(keyword)
        if with_comment:
            return (bcomment, iset)
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
    IDX_EMAIL_SPACE_1 = 2
    IDX_EMAIL_SPACE_2 = 3
    IDX_EMAIL_SPACE_3 = 4
    IDX_HISTORY_STATUS = 1000
    IDX_HISTORY_START = 1001
    IDX_HISTORY_END = 2000
    IDX_MAX_RESERVED = 2000

    IGNORE_SPECIAL_KW_RE = re.compile('(^\d+|[:@%"\'<>?!\._-]+)')
    IGNORE_NONLATIN_RE = re.compile('(^\d+|[\s:@%"\'<>?!\._-]+|'
        + '[^\u0000-\u007F\u0080-\u00FF\u0100-\u017F\u0180-\u024F])')

    def __init__(self, workdir,
            name='search', encryption_keys=None, defaults=None, maxint=0):

        self.records = RecordStore(os.path.join(workdir, name), name,
            salt=None, # FIXME: This must be set, OR ELSE
            aes_keys=encryption_keys or None,
            compress=96,
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
        logging.debug('Search engine config: %s' % (self.config,))

        try:
            self.part_spaces = [self.records[self.IDX_PART_SPACE], set()]
        except (KeyError, IndexError):
            self.part_spaces = [bytes(), set()]

        try:
            self.email_spaces = [
                self.records[self.IDX_EMAIL_SPACE_1],  # Recent only!
                set(),
                (self.records[self.IDX_EMAIL_SPACE_2], 'to'),
                (self.records[self.IDX_EMAIL_SPACE_3], 'from')]
        except (KeyError, IndexError):
            self.email_spaces = [
                bytes(), set(), ('to', bytes()), ('from', bytes())]

        self.history = self.records.get(self.IDX_HISTORY_STATUS) or {}
        self.l1_begin = self.IDX_MAX_RESERVED + 1
        self.l2_begin = self.l1_begin + self.config['l1_keywords']
        self.maxint = maxint
        self.deleted = IntSet()
        self.lock = threading.RLock()

        # Profiling...
        self.profile1 = self.profile2 = self.profile3 = 0

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

        self.magic_term_remap = {
            'is:recent': 'date:recent',
            'is:unread': 'in:unread'}

    def _allocate_history_slot(self):
        pos = self.history.get('pos', self.IDX_HISTORY_END) + 1
        if pos > self.IDX_HISTORY_END:
            pos = self.IDX_HISTORY_START
        self.history['pos'] = pos
        self.records[self.IDX_HISTORY_STATUS] = self.history
        return pos

    def delete_everything(self, *args):
        with self.lock:
            self.records.delete_everything(*args)

    def flush(self):
        with self.lock:
            return self.records.flush()

    def close(self):
        with self.lock:
            return self.records.close()

    def iter_tags(self, tag_namespace=''):
        if tag_namespace:
            tag_namespace = '@' + tag_namespace
        for idx in range(self.l1_begin, self.l2_begin):
            with self.lock:
                if idx not in self.records:
                    return
                plb = PostingListBucket(self.records.get(idx, cache=True))
                if not tag_namespace:
                    for kw, comment, iset in plb.items(decode=False):
                        kw = str(kw, 'utf-8')
                        if (kw[:3] == 'in:') and (kw[3] != '@'):
                            yield (kw, (comment, dumb_decode(iset)))
                else:
                    for kw, comment, iset in plb.items(decode=False):
                        kw = str(kw, 'utf-8')
                        if ((kw[:3] == 'in:') and (kw[3] != '@')
                               and kw.endswith(tag_namespace)):
                            kw = kw.split('@')[0]
                            yield (kw, (comment, dumb_decode(iset)))

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
        with self.lock:
            self.records[self.IDX_PART_SPACE] = self.part_spaces[0]
            return self.part_spaces[0]

    def part_space_count(self, term, min_hits):
        count = 0
        for hit in self[term]:
            if count >= min_hits:
                return True
        return False

    def email_space_count(self, term, min_hits):
        # FIXME: We need to search for something a bit different here.
        count = 0
        for hit in self[term]:
            if count >= min_hits:
                return True
        return False

    def update_terms(self, terms,
            min_hits=0, ignore_re=IGNORE_NONLATIN_RE, spaces=None):
        if spaces is None:
            spaces = self.part_spaces
        if spaces == self.email_spaces:
            counter = self.email_space_count
        else:
            counter = self.part_space_count

        updating = set(terms) | spaces[1]
        adding = set()
        removing = set()
        ignoring = set()
        for kw in sorted(list(updating)):
            kwb = bytes(kw, 'utf-8')
            if ignore_re:
                if ignore_re.search(kw):
                    ignoring.add(kwb)
                    continue
            if min_hits < 1:
                adding.add(kwb)
                continue
            if counter(term, min_hits):
                adding.add(kwb)
            else:
                removing.add(kwb)

        if adding or removing:
            blacklist = (removing | ignoring)
            for blob, wset in spaces[2:]:
                blacklist |= wset
                adding -= wset

        if adding or removing:
            spaces[0] = update_wordblob(adding, spaces[0],
                blacklist=blacklist,
                shortest=self.config['partial_shortest'],
                longest=self.config['partial_longest'],
                maxlen=self.config['partial_list_len'],
                lru=True)
            # FIXME: This becomes expensive if update batches are small!
            self.records[self.IDX_PART_SPACE] = spaces[0]

        spaces[1] = set()
        return spaces[0]

    def add_static_terms(self, wordlist, spaces=None):
        if spaces is None:
            spaces = self.part_spaces
        shortest = self.config['partial_shortest']
        longest = self.config['partial_longest']
        words = set([
            (bytes(w, 'utf-8') if isinstance(w, str) else w)
            for w in wordlist
            if shortest <= len(w) <= longest])
        spaces.append((create_wordblob(words,
                shortest=shortest,
                longest=longest,
                maxlen=len(words)+1),
            words))

    def add_dictionary_terms(self, dict_path, spaces=None):
        if spaces is None:
            spaces = self.part_spaces
        with open(dict_path, 'rb') as fd:
            blob = str(fd.read(), 'utf-8').lower()
            words = set([word.strip()
                for word in blob.splitlines()
                if not self.IGNORE_SPECIAL_KW_RE.search(word)])
        if words:
            self.add_static_terms(words, spaces=spaces)

    def candidates(self, keyword, max_results, spaces=None):
        if ' ' in keyword:
            prefix, keyword = keyword.split(' ', 1)
            prefix += ' '
        else:
            prefix = ''
        if spaces is None:
            spaces = self.part_spaces
        blobs = [spaces[0]]
        blobs.extend(blob for blob, words in spaces[2:])
        clist = wordblob_search(keyword, blobs, max_results)
        return [prefix+c for c in clist[:max_results]]

    def _empty_l1_idx(self):
        for idx in range(self.l1_begin, self.l2_begin):
            if idx not in self.records:
                return idx
        raise None

    def keyword_index(self, kw, prefer_l1=None, create=False):
        with self.lock:
            kw_hash = self.records.hash_key(kw)

            if (prefer_l1 is None) and (kw[:3] == 'in:'):
                prefer_l1 = True

            # This duplicates logic from records.py, but we want to avoid
            # hashing the key twice.
            kw_pos_idx = self.records.keys.get(kw_hash)
            if kw_pos_idx is not None:
                return kw_pos_idx[1]
            elif prefer_l1 and create:
                idx = self._empty_l1_idx()
                self.records.set_key(kw, idx)
                self.records[idx] = b''
                return idx

            kw_hash_int = struct.unpack('<I', kw_hash[:4])[0]
            kw_hash_int %= self.config['l2_buckets']
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

                    # Treat tag: prefix as alternatives to in: for tags.
                    if kw[:4] == 'tag:':
                        kw = 'in:' + kw[4:]

                    if tag_ns and (kw[:3] == 'in:'):
                        kw = '%s@%s' % (kw, tag_ns)
                    keywords[kw] = keywords.get(kw, []) + [r_id]
                if kw_list:
                    hits.append(r_id)

        kw_idx_list = [
            (self.keyword_index(kw, prefer_l1=prefer_l1, create=True), kw)
            for kw in keywords]

        return kw_idx_list, keywords, hits

    def _ns(self, k, ns):
        if ns and (k[:3] == 'in:'):
            if '@' in k:
                raise PermissionError('Namespace is fixed')
            return '%s@%s' % (k, ns)
        return k

    def rename_l1(self, kw, new_kw, tag_namespace=''):
        kw = self._ns(kw, tag_namespace)
        new_kw = self._ns(new_kw, tag_namespace)
        kw_pos, kw_idx = self.records.keys[self.records.hash_key(kw)]
        with self.lock:
            self.records.cache = {}  # Drop cache
            plb = PostingListBucket(self.records.get(kw_idx) or b'')
            bcom, iset = plb.remove(kw)
            plb.set(new_kw, iset, comment=bcom)
            self.records[kw_idx] = plb.blob
            self.records.set_key(new_kw, kw_idx)
            self.records.del_key(kw)

    def rename_tag(self, tag, new_tag, tag_namespace=''):
        return self.rename_l1(tag, new_tag, tag_namespace)

    def set_tag_comment(self, tag, comment, tag_namespace=''):
        tag = self._ns(tag, tag_namespace)
        with self.lock:
            idx = self.keyword_index(tag)
            plb = PostingListBucket(self.records.get(idx) or b'')
            plb.set_comment(tag, comment)
            self.records[idx] = plb.blob

    def get_tag(self, tag, tag_namespace=''):
        tag = self._ns(tag, tag_namespace)
        with self.lock:
            idx = self.keyword_index(tag)
            plb = PostingListBucket(self.records.get(idx) or b'')
        return plb.get(tag, with_comment=True)

    def mutate(self, mlist, record_history=None, tag_namespace=''):
        def _op(o):
            o = {'+': IntSet.Or,
                b'+': IntSet.Or,
                 '-': IntSet.Sub,
                b'-': IntSet.Sub}.get(o, o)
            if o not in (IntSet.Or, IntSet.Sub):
                raise ValueError('Unsupported op: %s' % o)
            return o

        def _op_kwi(op, kw):
            op = _op(op)
            if (kw == '*'):
                return (op, kw, None)
            else:
                kw = self._ns(kw, tag_namespace)
                return (op, kw, self.keyword_index(kw, create=(op==IntSet.Or)))

        slot = None
        cset_all = IntSet()
        changes = []
        mutations = 0
        with self.lock:
            for mset, op_kw_list in mlist:
                op_idx_kw_list = [_op_kwi(op, kw) for op, kw in op_kw_list]

                for op, kw, idx in op_idx_kw_list:
                    plb = PostingListBucket(self.records.get(idx) or b'')
                    iset = plb.get(kw)
                    if iset is None:
                        iset = IntSet()

                    oset = op(iset, mset)

                    if iset != oset:
                        plb.set(kw, oset)
                        self.records[idx] = plb.blob
                        mutations += 1

                        # Only keep history and report results regarding the
                        # mutation itself, to save space (zeros compress well)
                        # and avoid leaking data from outside our tag namespace.
                        # We assume the mset has already been scoped.
                        cset = IntSet()
                        cset |= iset
                        cset ^= oset  # XOR tells us which bits changed
                        cset &= mset  # Scope
                        iset &= mset  # Scope
                        changes.append((kw, idx, iset, cset))
                        cset_all |= cset

            if record_history:
                # Allocate slot while still locked, then release.
                slot = self._allocate_history_slot()

        if record_history:
            changes = {
                'id': '%.3x-%x' % (slot, random.randint(0, 0xffffffffff)),
                'comment': record_history,
                'changes': [
                    [kw, idx, dumb_encode_asc(iset), dumb_encode_asc(oset)]
                    for kw, idx, iset, oset in changes]}
            self.records[slot] = changes

        return {
            'mutations': mutations,
            'changed': cset_all,
            ('history' if record_history else 'changes'): changes}

    def profile_updates(self, which, t0, t1, t2, t3):
        p1 = int((t1 - t0) * 1000)
        p2 = int((t2 - t1) * 1000)
        p3 = int((t3 - t2) * 1000)
        self.profile1 += p1
        self.profile2 += p2
        self.profile3 += p3
        logging.debug(
            'Profiling(%s): prep/write/update .. now(%d/%d/%d) total(%d/%d/%d)'
            % (which, p1, p2, p3, self.profile1, self.profile2, self.profile3))

    def del_results(self, results, tag_namespace=''):
        t0 = time.time()
        (kw_idx_list, keywords, hits
            ) = self._prep_results(results, False, tag_namespace)
        t1 = time.time()
        for idx, kw in sorted(kw_idx_list):
            with self.lock:
                plb = PostingListBucket(self.records.get(idx) or b'')
                plb.deleted = IntSet(copy=self.deleted)
                plb.deleted |= keywords[kw]
                plb.add(kw, [])
                self.records[idx] = plb.blob
        t2 = time.time()
        self.update_terms(keywords)
        self.profile_updates('-%d' % len(kw_idx_list), t0, t1, t2, time.time())
        return {'keywords': len(keywords), 'hits': hits}

    def add_results(self, results, prefer_l1=None, tag_namespace=''):
        t0 = time.time()
        (kw_idx_list, keywords, hits
            ) = self._prep_results(results, prefer_l1, tag_namespace)
        t1 = time.time()
        for idx, kw in sorted(kw_idx_list):
            with self.lock:
                # These are instances of PostingList
                plb = PostingListBucket(self.records.get(idx) or b'')
                plb.deleted = self.deleted
                plb.add(kw, keywords[kw])
                self.records[idx] = plb.blob
        t2 = time.time()
        self.part_spaces[1] |= set(keywords.keys())
        self.profile_updates('+%d' % len(kw_idx_list), t0, t1, t2, time.time())
        return {'keywords': len(keywords), 'hits': hits}

    def __getitem__(self, keyword):
        idx = self.keyword_index(keyword)
        plb = PostingListBucket(self.records.get(idx) or b'')
        return plb.get(keyword) or IntSet()

    def _id_list(self, ids):
        try:
            if ids[0] in ('I', 'S', 'T', 'Z'):
                ids = dumb_decode(ids)
            else:
                elems = ids.split(',')
                ids = []
                for _id in elems:
                    if '..' in _id:
                        b, e = _id.split('..')
                        ids.extend(range(int(b), int(e)+1))
                    else:
                        ids.append(int(_id))
        except ValueError:
            ids = []
        return ids

    def _search(self, term, tag_ns):
        if isinstance(term, tuple):
            if len(term) > 1:
                op = term[0]
                return op(*[self._search(t, tag_ns) for t in term[1:]])
            else:
                return IntSet()

        if isinstance(term, str):
            # Treat tag: prefix as alternative to in: for tags.
            if term[:4] == 'tag:':
               term = 'in:' + term[4:]

            if tag_ns and (term[:3] == 'in:'):
               return self['%s@%s' % (term, tag_ns)]
            elif term in ('in:', 'all:mail', '*'):
               term = IntSet.All
            elif term[:3] == 'id:' or term[:4] == 'mid:':
               return IntSet(self._id_list(term.split(':', 1)[1]))
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

    def search(self, terms,
            tag_namespace='',
            mask_deleted=True, mask_tags=None, more_terms=None,
            explain=False):
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
        if more_terms:
            if isinstance(more_terms, str):
                more_terms = self.parse_terms(more_terms, self.magic_map)
            ops = (IntSet.And, ops, more_terms)
        if tag_namespace:
            # Explicitly search for "all:mail", to avoid returning results
            # from outside the namespace (which would otherwise happen when
            # searching without any tags at all).
            ops = (IntSet.And, IntSet.All, ops)
        if mask_tags:
            # Certain search results are excluded by default, unless they
            # were specifically requested in the query itself.
            masking = [tag for tag in mask_tags if tag not in terms]
            if masking:
                ops = tuple([IntSet.Sub, ops] + masking)
        with self.lock:
            rv = self._search(ops, tag_namespace)
            if mask_deleted:
                rv = IntSet.Sub(rv, self.deleted)
        if explain:
            rv = (tag_namespace, ops, rv)
        return rv

    def search_tags(self, search_set, tag_namespace=''):
        """
        Search for tags that match a search (terms or tuple) or result set
        (IntSet or list of ints).

        Returns a dictionary of (tag => (comment, IntSet)) mappings.
        """
        if isinstance(search_set, (tuple, str)):
            search_set = self.search(search_set, tag_namespace=tag_namespace)
        if not isinstance(search_set, IntSet):
            iset = IntSet()
            iset |= search_set
            search_set = iset
        results = {}
        for tag, (bcom, iset) in self.iter_tags(tag_namespace=tag_namespace):
            iset &= search_set
            if iset:
                results[tag] = (bcom, iset)
        return results

    def magic_terms(self, term):
        term = self.magic_term_remap.get(term, term)
        what = term.split(':')[0].lower()
        magic = self.magic_term_map.get(what)
        if magic is not None:
            return magic(term)

        # FIXME: Convert to:me, from:me into e-mail searches
        # Notmuch's thread-subqueries are kinda neat, implement them?

        return term

    def magic_emails(self, term):
        return term  # FIXME: A no-op

    def magic_candidates(self, term):
        if term == '*':
            return term

        max_results = self.config.get('partial_matches', 10)
        matches = self.candidates(term, max_results)
        if len(matches) > 1:
            #logging.debug('Expanded %s(<%d) to %s' % (term, max_results, matches))
            return tuple([IntSet.Or] + matches)
        else:
            return matches[0]


if __name__ == '__main__':
  try:
    pl = PostingListBucket(b'', compress=128)
    pl.add('hello', [1, 2, 3, 4])
    assert(isinstance(pl.get('hello'), IntSet))
    assert(pl.get('floop') is None)
    assert(1 in pl.get('hello'))
    assert(5 not in pl.get('hello'))
    pl.add('hello', [5])
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
        (4, ['in:inbox', 'in:testing', 'in:testempty', 'in:bjarni'])])
    se.add_results([
        (5, ['in:inbox', 'please'])],
        tag_namespace='work')

    se.deleted |= 0
    assert(list(se.search(IntSet.All)) == [1, 2, 3, 4, 5])

    assert(3 in se.search('please'))
    assert(5 in se.search('please'))
    assert(5 in se.search('please', tag_namespace='work'))
    assert(3 not in se.search('please', tag_namespace='work'))

    # Make sure tags go to l1, others to l2.
    assert(se.keyword_index('in:bjarni') < se.l2_begin)
    assert(se.keyword_index('in:inbox') < se.l2_begin)
    assert(se.keyword_index('please') >= se.l2_begin)

    # We can enumerate our tags and set metadata on them!
    se.set_tag_comment('in:bjarni', 'Hello world')
    assert(se.get_tag('in:bjarni')[0] == b'Hello world')
    assert('in:bjarni'         in dict(se.iter_tags()))
    assert('in:inbox@work'     in dict(se.iter_tags()))
    assert('in:bjarni'     not in dict(se.iter_tags(tag_namespace='work')))
    assert('in:inbox'          in dict(se.iter_tags(tag_namespace='work')))
    assert('in:inbox@work' not in dict(se.iter_tags(tag_namespace='work')))
    assert(not se.search_tags([55]))
    assert('in:inbox' in se.search_tags([4, 55]))
    assert('in:inbox' not in se.search_tags('please'))
    assert('in:inbox' in se.search_tags('please', tag_namespace='work'))

    assert(3 in se.search('remove'))
    se.del_results([(3, ['please'])])
    assert(3 not in se.search('please'))
    assert(3 in se.search('remove'))

    assert(5 in se.search('in:inbox', tag_namespace='work'))
    assert(5 in se.search('all:mail', tag_namespace='work'))
    assert(4 not in se.search('all:mail', tag_namespace='work'))
    assert(3 not in se.search('in:inbox'))
    assert(4 in se.search('in:testing'))

    mr = se.mutate([
        (IntSet([4, 3]), [('-', 'in:testing'), (IntSet.Or, 'in:inbox')]),
        ], record_history='Testing')

    assert(5 not in se.search('in:imaginary'))
    mr2 = se.mutate([
        (IntSet([5, 6]), [('+', 'in:imaginary')])
        ], record_history='Test2')
    assert(5 in se.search('in:imaginary'))

    slot = int(mr['history']['id'].split('-')[0], 16)
    assert(slot == se.IDX_HISTORY_START)
    lh = se.records.get(slot, decode=True)
    assert(lh['comment'] == 'Testing')
    assert(lh == mr['history'])

    assert(mr['mutations'] == 2)
    assert('in:testing' == mr['history']['changes'][0][0])
    assert(4 not in se.search('in:testing'))
    assert(3 in se.search('in:inbox'))
    assert(4 in se.search('in:inbox'))
    assert(4 not in se.search('in:inbox', tag_namespace='work'))
    se.rename_tag('in:inbox', 'in:outbox')
    assert(4 in se.search('in:outbox'))
    assert(4 not in se.search('in:inbox'))

    # Test reducing a set to empty and then adding back to it
    se.del_results([(4, ['in:testempty'])])
    assert(4 not in se.search('in:testempty'))
    se.add_results([(4, ['in:testempty'])])
    assert(4 in se.search('in:testempty'))

    print('Tests pass OK (1/3)')

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
        assert(se.candidates('the orang*', 10) == ['the orang', 'the orange'])
        se.add_dictionary_terms('/usr/share/dict/words')
        assert('additional' in se.candidates('addit*', 20))

        se.records.compact()
        print('Tests pass OK (%d/3)' % (round+2,))

    #import time
    #time.sleep(10)
  finally:
    se.delete_everything(True, False, True)
