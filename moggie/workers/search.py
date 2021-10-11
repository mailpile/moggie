import json
import traceback

from .base import BaseWorker


class SearchWorker(BaseWorker):
    """
    """

    KIND = 'search'

    def __init__(self, status_dir, engine_dir, encryption_key, maxint,
            name=KIND, defaults=None):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'term_search': (b'Ud', self.api_term_search),
            b'search':      (b'U',  self.api_search)})

        self.encryption_key = encryption_key
        self.engine_dir = engine_dir
        self.defaults = defaults
        self.maxint = maxint
        self.engine = None

    def _main_httpd_loop(self):
        from ..search import SearchEngine
        self.engine = SearchEngine(self.engine_dir,
            name=self.name,
            encryption_key=self.encryption_key,
            defaults=self.defaults,
            maxint=self.maxint)
        return super()._main_httpd_loop()

    def term_search(self, term, count=10):
        return self.call('term_search', term, count)

    def search(self, terms):
        return self.call('search', terms)

    def api_term_search(self, term, count, **kwargs):
        self.reply_json(self.engine.candidates(term, count))

    def api_search(self, terms, **kwargs):
        print('Search for %s' % (self.engine.parse_terms(terms),))


if __name__ == '__main__':
    sw = SearchWorker('/tmp', '/tmp', b'1234', 0, name='moggie-sw-test').connect()
    if sw:
        try:
            print(sw.term_search('hello'))
            print(sw.search('hello + world'))

            #assert(b'345' == fd.read())

            print('** Tests passed, waiting... **')
            sw.join()
        finally:
            sw.terminate()

