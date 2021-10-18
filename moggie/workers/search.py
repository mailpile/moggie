import json
import time
import traceback
import threading

from ..util.dumbcode import dumb_encode_asc
from .base import BaseWorker


class SearchWorker(BaseWorker):
    """
    """

    KIND = 'search'

    def __init__(self, status_dir, engine_dir, encryption_key, maxint,
            name=KIND, defaults=None):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'add_results': (True, self.api_add_results),
            b'del_results': (True, self.api_del_results),
            b'term_search': (True, self.api_term_search),
            b'explain':     (True, self.api_explain),
            b'search':      (True, self.api_search)})

        self.encryption_key = encryption_key
        self.engine_dir = engine_dir
        self.defaults = defaults
        self.maxint = maxint
        self.engine = None

    def _main_httpd_loop(self):
        from ..search.engine import SearchEngine, explain_ops
        self._explain_ops = explain_ops
        self._engine = SearchEngine(self.engine_dir,
            name=self.name,
            encryption_key=self.encryption_key,
            defaults=self.defaults,
            maxint=self.maxint)
        return super()._main_httpd_loop()

    def add_results(self, results, callback_chain=None):
        return self.call('add_results', results, callback_chain)

    def del_results(self, results, callback_chain=None):
        return self.call('del_results', results, callback_chain)

    def term_search(self, term, count=10):
        return self.call('term_search', term, count)

    def explain(self, terms):
        return self.call('explain', terms)

    def search(self, terms):
        return self.call('search', terms)

    def api_add_results(self, results, callback_chain, **kwargs):
        if kwargs.get('wait') and not callback_chain:
            self.reply_json(self._engine.add_results(results))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                rv = self._engine.add_results(results)
                self.results_to_callback_chain(callback_chain, rv)
            self.add_background_job(background_add_results)

    def api_del_results(self, results, callback_chain, **kwargs):
        if kwargs.get('wait') and not callback_chain:
            self.reply_json(self._engine.del_results(results))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                rv = self._engine.del_results(results)
                self.results_to_callback_chain(callback_chain, rv)
            self.add_background_job(background_add_results)

    def api_term_search(self, term, count, **kwargs):
        self.reply_json(self._engine.candidates(term, count))

    def api_explain(self, terms, **kwargs):
        self.reply_json(self._engine.explain(terms))

    def api_search(self, terms, **kwargs):
        ops, hits = self._engine.search(terms,
            mask_deleted=kwargs.get('mask_deleted', True),
            explain=True)
        self.reply_json({
            'terms': terms,
            'query': self._explain_ops(ops),
            'hits': dumb_encode_asc(hits, compress=1024)})


if __name__ == '__main__':
    sw = SearchWorker('/tmp', '/tmp', b'1234', 0, name='moggie-sw-test').connect()
    if sw:
        print('URL: %s' % sw.url)
        try:
            print(sw.add_results([[1, ["hello", "world"]], [1024, ["hello"]]],
                callback_chain=[sw.callback_url('ping')]))
            print(sw.term_search('hello'))
            print(sw.search('hello + world'))

            #assert(b'345' == fd.read())

            print('** Tests passed, waiting... **')
            sw.join()
        finally:
            sw.terminate()

