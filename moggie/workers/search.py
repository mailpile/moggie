import json
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

    def __init__(self, status_dir, engine_dir, maxint, encryption_keys,
            name=KIND, defaults=None, notify=None, log_level=logging.ERROR):

        BaseWorker.__init__(self, status_dir,
            name=name, notify=notify, log_level=log_level)
        self.functions.update({
            b'add_results':  (True, self.api_add_results),
            b'del_results':  (True, self.api_del_results),
            b'mutate':       (True, self.api_mutate),
            b'compact':      (True, self.api_compact),
            b'update_terms': (True, self.api_update_terms),
            b'term_search':  (True, self.api_term_search),
            b'explain':      (True, self.api_explain),
            b'search':       (True, self.api_search)})

        self.change_lock = threading.Lock()
        self.encryption_keys = encryption_keys
        self.engine_dir = engine_dir
        self.defaults = defaults
        self.maxint = maxint
        self._engine = None

    def _main_httpd_loop(self):
        from ..search.engine import SearchEngine, explain_ops
        self._explain_ops = explain_ops
        self._engine = SearchEngine(self.engine_dir,
            name=self.name,
            encryption_keys=self.encryption_keys,
            defaults=self.defaults,
            maxint=self.maxint)

        for dpath in self.SYS_DICTIONARY_PATHS:
            if os.path.exists(dpath):
                try:
                    self._engine.add_dictionary_terms(dpath)
                    break
                except:
                    pass

        del self.encryption_keys

        return super()._main_httpd_loop()

    def add_results(self, results, callback_chain=None, wait=True):
        return self.call('add_results', results, callback_chain, wait)

    def del_results(self, results, callback_chain=None, wait=True):
        return self.call('del_results', results, callback_chain, wait)

    def mutate(self, mset, op_kw_list):
        strop_kw_list = [[self._OP_STR_MAP(op), kw] for op, kw in op_kw_list]
        return self.call('mutate', mset, strop_kw_list)

    def compact(self, full=False):
        return self.call('compact', full, None)

    def update_terms(self, terms):
        return self.call('update_terms', terms)

    def term_search(self, term, count=10):
        return self.call('term_search', term, count)

    def explain(self, terms):
        return self.call('explain', terms)

    def search(self, terms, tag_namespace=None, mask_deleted=True):
        return self.call('search', terms, mask_deleted, tag_namespace)

    def intersect(self, terms, hits, tag_namespace=None, mask_deleted=True):
        srch = self.call('search', terms, mask_deleted, tag_namespace)
        return IntSet.And(dumb_decode(srch['hits']), hits)

    def api_mutate(self, mset, strop_kw_list, **kwargs):
        op_kw_list = [(self._STR_OP_MAP(op), kw) for op, kw in strop_kw_list]
        with self.change_lock:
            self.reply_json(self._engine.mutate(mset, op_kw_list))

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

    def api_search(self, terms, mask_deleted, tag_namespace, **kwargs):
        tns, ops, hits = self._engine.search(terms,
            tag_namespace=tag_namespace,
            mask_deleted=mask_deleted,
            explain=True)
        result = {
            'terms': terms,
            'tag_namespace': tns,
            'query': self._explain_ops(ops),
            'hits': dumb_encode_asc(hits, compress=256)}
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

