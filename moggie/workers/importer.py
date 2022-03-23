import json
import os
import time
import traceback
import threading

from ..jmap.requests import to_jmap_request
from ..util.dumbcode import dumb_encode_asc
from ..storage.files import FileStorage
from .base import BaseWorker


class ImportWorker(BaseWorker):
    """
    """
    KIND = 'import'
    NICE = 20

    BATCH_SIZE = 100

    def __init__(self, status_dir,
            app_worker=None, search_worker=None, name=KIND, defaults=None):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'import_search': (True, self.api_import_search)})
        self.app = app_worker
        self.search = search_worker
        assert(self.app and self.search)

    def import_search(self, request_obj, initial_tags,
            callback_chain=[], force=False):
        return self.call('import_search',
            request_obj, initial_tags, force, callback_chain)

    def api_import_search(self,
            request, initial_tags, force, callback_chain, **kwargs):
        request_obj = to_jmap_request(request)
        if not callback_chain:
            self.reply_json(
                self._import_search(request_obj, initial_tags, force))
        else:
            self.reply_json({'running': True})
            def background_import_search():
                rv = self._import_search(request_obj, initial_tags, force,
                        callback_chain=callback_chain)
            self.add_background_job(background_import_search)

    def _index_full_messages(self, emails, callback_chain):
        # Full indexing, per message in "in:incoming":
        #
        #   1. Submit a request to the main app to fetch the e-mail's
        #      text parts and structure (not full attachments). Again,
        #      we don't know or care where the mail is coming from.
        #   2. Generate keywords and tags
        #   3. Run the filtering logic to mutate keywords/tags
        #   4. Add/remove results from the search engine
        #...
        print('FIXME: Should index: %s' % emails)
        # NOTE: Should abort if we see self.keep_running go false, and
        #       trust messages will be picked up again later.

    def _import_search(self, request_obj, initial_tags, force,
            callback_chain=None):

        def _full_indexer(emails):
            def _full_index():
                self._index_full_messages(emails, callback_chain)
            return _full_index

        tags = ['in:incoming'] + initial_tags
        done = False
        progress = 0
        while self.keep_running and not done:
            # 1. Submit a limited request_obj to the main app worker
            #    (The app is responsible for selecting the right backend
            #    mail source to process the request, we don't need to know
            #    where things are coming from)
            response = self.app.jmap(request_obj.update({
                'skip': progress,
                'limit': self.BATCH_SIZE}))
            emails = response['emails']
            progress += len(emails)
            done = (len(emails) < self.BATCH_SIZE)

            # 2. Forward received messages to search engine for initial
            #    import (add as new, assign in:incoming and namespace tags).
            added = self.search.add_results([[md, tags] for md in emails])
            print('added = %s' % added)

            # 3. When search engine reports success, schedule full indexing
            #    and filtering of that batch of messages.
            self.add_background_job(_full_indexer(emails), which='full')

            # 4. Repeat until all mail is processed, report progress
            if callback_chain:
                self.results_to_callback_chain(
                    callback_chain, {'added': progress})

        # FIXME: error handling? ... what does that look like? Ugh.
        #        That's where this whole architecture is failing.

        return {'added': progress}

# HMM, looks like chains need error endpoints as well.


if __name__ == '__main__':
    import sys
    from ..jmap.requests import RequestMailbox
    from ..jmap.responses import ResponseMailbox
    from ..email.metadata import Metadata

    class MockAppWorker:
        def jmap(self, request_obj):
            print('jmap: %s' % request_obj)
            return ResponseMailbox(request_obj, [
                Metadata.ghost('<ghost1@moggie>')
                ], False)

    class MockSearchWorker:
        def add_results(self, request_obj):
            print('add_results: %s' % request_obj)
            return {}

    iw = ImportWorker('/tmp',
             app_worker=MockAppWorker(),
             search_worker=MockSearchWorker(),
             name='moggie-imp-test').connect()
    if iw:
        print('URL: %s' % iw.url)
        try:
            iw.import_search(RequestMailbox(
                mailbox='/home/bre/Mail/klaki/2021-10.mbx'),
                ['in:fairyland'],
                callback_chain=[iw.callback_url('noop')])
            time.sleep(0.6)

            if 'wait' in sys.argv[1:]:
                print('** Tests passed, waiting... **')
            else:
                iw.quit()
                print('** Tests passed, exiting... **')
            iw.join()
        finally:
            iw.terminate()

