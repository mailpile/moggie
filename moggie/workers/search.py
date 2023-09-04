import logging
import os
import time
import traceback
import threading

from ..util.dumbcode import dumb_encode_asc, dumb_decode
from ..util.intset import IntSet
from .base import BaseWorker


class SearchWorker(BaseWorker):
    """
    """

    KIND = 'search'

    SYS_DICTIONARY_PATHS = [
        '/etc/dictionaries-common/words',
        '/usr/share/dict/words']

    MASK_TAGS = ('in:trash', 'in:spam')

    EXACT_SEARCHES = ('msgid', 'message-id', 'id', 'mid')

    SORT_NONE = 0
    SORT_DATE_ASC = 1
    SORT_DATE_DEC = 2

    _OP_STR_MAP = {
        IntSet.Or: 'OR',
        IntSet.And: 'AND',
        IntSet.Sub: 'SUB'}
    _STR_OP_MAP = {
        'OR': IntSet.Or,
        'AND': IntSet.And,
        'SUB': IntSet.Sub}

    @classmethod
    def Connect(cls, status_dir):
        return cls(status_dir, None, None, None).connect(autostart=False)

    def __init__(self, status_dir, engine_dir, metadata, encryption_keys,
            name=KIND, defaults=None, notify=None, log_level=logging.ERROR):

        BaseWorker.__init__(self, status_dir,
            name=name, notify=notify, log_level=log_level)
        self.functions.update({
            b'add_results':  (True, self.api_add_results),
            b'del_results':  (True, self.api_del_results),
            b'tag':          (True, self.api_tag),
            b'compact':      (True, self.api_compact),
            b'update_terms': (True, self.api_update_terms),
            b'term_search':  (True, self.api_term_search),
            b'explain':      (True, self.api_explain),
            b'search':       (True, self.api_search)})

        self.change_lock = threading.Lock()
        self.encryption_keys = encryption_keys
        self.engine_dir = engine_dir
        self.defaults = defaults
        self.metadata = metadata
        self.maxint = metadata.info()['maxint']
        self._engine = None

    def quit(self, *args, **kwargs):
        with self.change_lock:
            with self._engine.lock:
                super().quit(*args, **kwarg)

    def _main_httpd_loop(self):
        from ..search.engine import SearchEngine, explain_ops
        self._explain_ops = explain_ops
        self._engine = SearchEngine(self.engine_dir,
            name=self.name,
            encryption_keys=self.encryption_keys,
            defaults=self.defaults,
            maxint=self.maxint)

        self._engine.magic_term_map.update({
            'tid': self._magic_thread,
            'thread': self._magic_thread})

        for dpath in self.SYS_DICTIONARY_PATHS:
            if os.path.exists(dpath):
                try:
                    self._engine.add_dictionary_terms(dpath)
                    break
                except:
                    pass

        del self.encryption_keys

        return super()._main_httpd_loop()

    def _magic_thread(self, thread_id):
        tid = None
        try:
            tid = int(thread_id.split(':')[-1])
            info = self.metadata.metadata([tid], threads=True, only_ids=True)
            if info:
                ids = ','.join(('%s' % i) for i in info[0]['messages'])
                return 'id:%s' % ids
        except:
            logging.warning('Failed to load thread message IDs for %s' % thread_id)
        return thread_id if tid is None else 'id:%s' % (tid)

    def add_results(self, results, callback_chain=None, wait=True):
        return self.call('add_results', results, callback_chain, wait)

    def del_results(self, results, callback_chain=None, wait=True):
        return self.call('del_results', results, callback_chain, wait)

    def compact(self, full=False, callback_chain=None):
        return self.call('compact', full, callback_chain)

    def update_terms(self, terms):
        return self.call('update_terms', terms)

    def term_search(self, term, count=10):
        return self.call('term_search', term, count)

    def explain(self, terms):
        return self.call('explain', terms)

    async def async_search(self, loop, terms,
            tag_namespace=None,
            mask_deleted=True, mask_tags=None, more_terms=None,
            with_tags=False):
        return await self.async_call(loop, 'search', terms,
            mask_deleted, mask_tags, more_terms, tag_namespace, with_tags)

    def search(self, terms,
            tag_namespace=None,
            mask_deleted=True, mask_tags=None, more_terms=None,
            with_tags=False):
        return self.call('search', terms,
            mask_deleted, mask_tags, more_terms, tag_namespace, with_tags)

    async def async_intersect(self, loop, terms, hits,
            tag_namespace=None,
            mask_deleted=True, mask_tags=None, more_terms=None):
        srch = await self.async_call(loop, 'search', terms,
            mask_deleted, mask_tags, more_terms, tag_namespace, False)
        return IntSet.And(dumb_decode(srch['hits']), hits)

    def intersect(self, terms, hits,
            tag_namespace=None,
            mask_deleted=True, mask_tags=None, more_terms=None):
        srch = self.call('search', terms,
            mask_deleted, mask_tags, more_terms, tag_namespace, False)
        return IntSet.And(dumb_decode(srch['hits']), hits)

    async def async_tag(self, loop, tag_op_sets, record_history=None,
            tag_namespace=None,
            tag_redo_id=None, tag_undo_id=None,
            mask_deleted=True, mask_tags=False, more_terms=None):
        tag_op_sets = list(tag_op_sets)
        for i, (tag_ops, m) in enumerate(tag_op_sets):
            if isinstance(m, list):
                tag_op_sets[i] = (tag_ops, IntSet(m))
        return await self.async_call(loop, 'tag',
            tag_op_sets, record_history, tag_namespace,
            tag_redo_id, tag_undo_id,
            mask_deleted, mask_tags, more_terms)

    def tag(self, tag_op_sets, record_history=None,
            tag_namespace=None,
            tag_redo_id=None, tag_undo_id=None,
            mask_deleted=True, mask_tags=False, more_terms=None):
        tag_op_sets = list(tag_op_sets)
        for i, (tag_ops, m) in enumerate(tag_op_sets):
            if isinstance(m, list):
                tag_op_sets[i] = (tag_ops, IntSet(m))
#            if isinstance(m, (IntSet, list)):
#                tag_op_sets[i] = (
#                    tag_ops,
#                    dumb_encode_asc(IntSet(m), compress=256))
#            if isinstance(m, dict):
#                tag_op_sets[i] = (tag_ops, m)
#            elif isinstance(m, str):
#                r = self.call('search', m,
#                              mask_deleted, mask_tags, more_terms,
#                              tag_namespace, False)
#                tag_op_sets[i] = (tag_ops, r['hits'])
#            else:
#                tag_op_sets[i] = (tag_ops, dumb_encode_asc(IntSet(m)))
        return self.call('tag',
            tag_op_sets, record_history, tag_namespace,
            tag_redo_id, tag_undo_id,
            mask_deleted, mask_tags, more_terms)

    def api_tag(self,
            tag_op_sets, rec_hist, tag_namespace,
            tag_redo_id, tag_undo_id,
            mask_deleted, mask_tags, more_terms, **kwa):
        if tag_namespace:
            tag_namespace = tag_namespace.lower()

        if tag_redo_id:
            mutations = self._engine.historic_mutations(tag_redo_id, redo=True)
        elif tag_undo_id:
            mutations = self._engine.historic_mutations(tag_undo_id, undo=True)
        else:
            mutations = []

        no_hits = dumb_encode_asc(IntSet([]), compress=256)
        for (tag_ops, m) in tag_op_sets:
            mutation = [m, []]
            if not isinstance(m, (IntSet, list, dict)):
                m = self.api_search(m,
                        mask_deleted, mask_tags, more_terms,
                        tag_namespace, False,
                        _internal=True)
                mutation[0] = m['hits'] if (m and m['hits']) else None

            for tag_op in tag_ops:
                tag = tag_op[1:]
                if ':' in tag:
                    tag = 'in:' + tag.split(':')[1]
                else:
                    tag = 'in:' + tag
                if tag_op[:1] == '+':
                    mutation[1].append(
                        [IntSet.Or, self._engine.tag_quote_magic(tag)])
                    if tag_namespace:
                        mutation[1].append([IntSet.Or, 'in:'])
                    elif '@' in tag:
                        ns = tag.split('@', 1)[1]
                        mutation[1].append([IntSet.Or, 'in:@'+ns])
                elif tag_op[:1] == '-':
                    mutation[1].append(
                        [IntSet.Sub, self._engine.tag_quote_magic(tag)])
                else:
                    raise ValueError('Invalid tag op: %s' % tag_op)

            if mutation[0] and mutation[1]:
                mutations.append(mutation)

        with self.change_lock:
            result = self._engine.mutate(
                mutations,
                record_history=rec_hist,
                tag_namespace=tag_namespace)
            result['changed'] = dumb_encode_asc(result['changed'], compress=256)
            self.reply_json(result)

    def api_compact(self, full, callback_chain, **kwargs):
        def report_progress(progress):
            progress['full'] = full
            self.notify('[search] Compacting: %s' % (progress,), data=progress)
            self.results_to_callback_chain(callback_chain, progress)
        def background_compact():
            with self.change_lock:
                self._engine.records.compact(
                    partial=not full,
                    progress_callback=report_progress)
        self.add_background_job(background_compact)
        self.reply_json({'running': True})

    def api_add_results(self, results, callback_chain, wait, **kwargs):
        if wait and not callback_chain:
            with self.change_lock:
                self.reply_json(self._engine.add_results(results))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                with self.change_lock:
                    rv = self._engine.add_results(results)
                self.results_to_callback_chain(callback_chain, rv)
            self.add_background_job(background_add_results)

    def api_del_results(self, results, callback_chain, wait, **kwargs):
        if wait and not callback_chain:
            with self.change_lock:
                self.reply_json(self._engine.del_results(results))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                with self.change_lock:
                    rv = self._engine.del_results(results)
                self.results_to_callback_chain(callback_chain, rv)
            self.add_background_job(background_add_results)

    def api_update_terms(self, terms, **kwargs):
        self.reply_json({'FIXME': 1})

    def api_term_search(self, term, count, **kwargs):
        self.reply_json(self._engine.candidates(term, count))

    def api_explain(self, terms, **kwargs):
        self.reply_json(self._engine.explain(terms))

    def api_search(self,
            terms, mask_deleted, mask_tags, more_terms,
            tag_namespace, with_tags,
            _internal=False, **kwa):
        if tag_namespace:
            tag_namespace = tag_namespace.lower()

        if mask_tags is None:
            exact = [
                term for term in terms.replace('+', '').split(' ')
                if term.split(':', 1)[0] in self.EXACT_SEARCHES]
            if not exact:
                mask_tags = self.MASK_TAGS

        tns, ops, hits = self._engine.search(terms,
            tag_namespace=tag_namespace,
            mask_deleted=mask_deleted,
            mask_tags=mask_tags,
            more_terms=more_terms,
            explain=True)
        result = {
            'terms': terms,
            'more_terms': more_terms,
            'mask_deleted': mask_deleted,
            'mask_tags': mask_tags,
            'tag_namespace': tns,
            'query': self._explain_ops(ops)}

        logging.debug('Searched: %s' % result)
        if _internal:
            result['hits'] = hits
        else:
            result['hits'] = dumb_encode_asc(hits, compress=256)

        if with_tags:
            tag_info = self._engine.search_tags(
                hits, tag_namespace=tag_namespace)
            def _dec_comment(comment):
                try:
                    comment = str(comment, 'utf-8')
                    comment = from_json(comment) if comment else {}
                except (ValueError, TypeError):
                    pass
                return comment
            result['tags'] = dict(
                (tag, (_dec_comment(com), dumb_encode_asc(iset, compress=128)))
                for tag, (com, iset) in tag_info.items())

        if _internal:
            return result
        else:
            self.reply_json(result)


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.DEBUG)
    sw = SearchWorker('/tmp', '/tmp', 0, [b'1234'], name='moggie-sw-test').connect()
    if sw:
        print('URL: %s' % sw.url)
        msgid = '<this-is-a-ghost@moggie>'
        try:
            ghost = 1
            assert(sw.add_results([[ghost, ["spooky", "action"]]], wait=True))
            assert(sw.add_results([
                [ghost, ["spooky", "action"]],
                [ghost, ["spooky", "lives"]],
                [ghost, set(["spooky", "people"])],
                [256, ["hello", "world"]],
                [1024, ["hello"]]],
                callback_chain=[sw.callback_url('noop')]
                ) == {'running': True})
            assert(sw.term_search('hello') == ['hello'])
            time.sleep(1)

            s1 = sw.search('hello + world')
            assert(s1['terms'] == 'hello + world')
            assert(s1['query'] == '(hello OR world)')
            assert(list(dumb_decode(s1['hits'])) == [256, 1024])

            s2 = sw.search('spooky')
            assert(s2['terms'] == 'spooky')
            assert(s2['query'] == 'spooky')
            assert(list(dumb_decode(s2['hits'])) == [1])

            if 'wait' not in sys.argv[1:]:
                sw.quit()
                print('** Tests passed, exiting... **')
            else:
                print('** Tests passed, waiting... **')

            sw.join()
        finally:
            sw.terminate()

