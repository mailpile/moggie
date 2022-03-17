import json
import os
import time
import traceback
import threading

from ..email.metadata import Metadata
from ..util.dumbcode import dumb_encode_asc
from ..util.intset import IntSet
from .base import BaseWorker


class SearchWorker(BaseWorker):
    """
    """

    KIND = 'search'

    _OP_STR_MAP = {
        IntSet.Or: 'OR',
        IntSet.And: 'AND',
        IntSet.Sub: 'SUB'}
    _STR_OP_MAP = {
        'OR': IntSet.Or,
        'AND': IntSet.And,
        'SUB': IntSet.Sub}

    def __init__(self, status_dir, engine_dir, encryption_keys,
            name=KIND, defaults=None):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'add_results': (True, self.api_add_results),
            b'del_results': (True, self.api_del_results),
            b'mutate':      (True, self.api_mutate),
            b'term_search': (True, self.api_term_search),
            b'explain':     (True, self.api_explain),
            b'search':      (True, self.api_search)})

        self.encryption_keys = encryption_keys
        self.engine_dir = engine_dir
        self.defaults = defaults
        self._engine = None
        self._metadata = None

    def _main_httpd_loop(self):
        from ..search.engine import SearchEngine, explain_ops
        from ..storage.metadata import MetadataStore
        self._explain_ops = explain_ops
        self._metadata = MetadataStore(
            os.path.join(self.engine_dir, self.name + '-metadata'),
            'metadata',
            self.encryption_keys)
        self._engine = SearchEngine(self.engine_dir,
            name=self.name,
            encryption_keys=self.encryption_keys,
            defaults=self.defaults,
            maxint=len(self._metadata))
        del self.encryption_keys
        return super()._main_httpd_loop()

    def add_results(self, results, callback_chain=None):
        return self.call('add_results', results, callback_chain)

    def del_results(self, results, callback_chain=None):
        return self.call('del_results', results, callback_chain)

    def mutate(self, mset, op_kw_list):
        strop_kw_list = [[self._OP_STR_MAP(op), kw] for op, kw in op_kw_list]
        return self.call('mutate', mset, storp_kw_list)

    def term_search(self, term, count=10):
        return self.call('term_search', term, count)

    def explain(self, terms):
        return self.call('explain', terms)

    def search(self, terms, metadata=False):
        return self.call('search', terms, qs={'metadata': metadata})

    def api_mutate(self, mset, strop_kw_list):
        op_kw_list = [(self._STR_OP_MAP(op), kw) for op, kw in strop_kw_list]
        self.reply_json(self._engine.mutate(mset, op_kw_list))

    def api_add_results(self, results, callback_chain, **kwargs):
        def _idx_list():
            _res = []
            for _idx, kws in results:
                if isinstance(_idx, (list, Metadata)):
                    _idx = self._metadata.update_or_add(Metadata(*_idx))
                _res.append([_idx, kws])
            return _res
        if kwargs.get('wait') and not callback_chain:
            self.reply_json(self._engine.add_results(_idx_list()))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                rv = self._engine.add_results(_idx_list())
                self.results_to_callback_chain(callback_chain, rv)
            self.add_background_job(background_add_results)

    def api_del_results(self, results, callback_chain, **kwargs):
        def _res_list():
            return results
        if kwargs.get('wait') and not callback_chain:
            self.reply_json(self._engine.del_results(_res_list()))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                rv = self._engine.del_results(_res_list())
                self.results_to_callback_chain(callback_chain, rv)
            self.add_background_job(background_add_results)

    def api_term_search(self, term, count, **kwargs):
        self.reply_json(self._engine.candidates(term, count))

    def api_explain(self, terms, **kwargs):
        self.reply_json(self._engine.explain(terms))

    def api_search(self, terms, **kwargs):
        tns, ops, hits = self._engine.search(terms,
            tag_namespace=kwargs.get('tag_namespace', ''),
            mask_deleted=kwargs.get('mask_deleted', True),
            explain=True)
        result = {
            'terms': terms,
            'tag_namespace': tns,
            'query': self._explain_ops(ops),
            'hits': dumb_encode_asc(hits, compress=1024)}
        if kwargs.get('metadata'):
            result['metadata'] = md = []
            result['skip'] = skip = int(kwargs.get('skip', 0))
            result['limit'] = limit = int(kwargs.get('limit', 0)) or None
            for idx in hits:
                if skip > 0:
                    skip -= 1
                else: 
                    if limit is not None:
                        limit -= 1
                        if limit < 0:
                            break
                    md.append(self._metadata[idx])
        self.reply_json(result)


if __name__ == '__main__':
    sw = SearchWorker('/tmp', '/tmp', [b'1234'], name='moggie-sw-test').connect()
    if sw:
        print('URL: %s' % sw.url)
        ghost = Metadata.ghost('<this-is-a-ghost@moggie>')
        try:
            assert(sw.add_results([
                [ghost, ["spooky", "action"]],
                [ghost, ["spooky", "lives"]],
                [ghost, ["spooky", "people"]],
                [256, ["hello", "world"]],
                [1024, ["hello"]]],
                #callback_chain=[sw.callback_url('ping')]
                ) == {'running': True})
            assert(sw.term_search('hello') == ['hello'])
            s1 = sw.search('hello + world')
            print(s1)
            print(sw.search('spooky', metadata=True))

            print('** Tests passed, waiting... **')
            sw.quit()
            sw.join()
        finally:
            sw.terminate()

