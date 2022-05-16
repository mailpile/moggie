import json
import logging
import os
import time
import traceback
import threading

from ..jmap.requests import *
from ..util.dumbcode import dumb_encode_asc
from ..storage.files import FileStorage
from ..search.extractor import KeywordExtractor
from ..search.filters import FilterEngine, FilterError
from .base import BaseWorker


class ImportWorker(BaseWorker):
    """
    """
    KIND = 'import'
    NICE = 20
    BACKGROUND_TASK_SLEEP = 0

    COMPACT_INTERVAL = 5000  # Keeps us from growing without bounds
    BATCH_SIZE = 250

    def __init__(self, status_dir,
            app_worker=None,
            fs_worker=None,
            search_worker=None,
            metadata_worker=None,
            name=KIND):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'import_search': (True, self.api_import_search)})

        self.fs = fs_worker
        self.app = app_worker
        self.search = search_worker
        self.metadata = metadata_worker
        self.imported = 0
        self.compacted = 0

        self.kwe = KeywordExtractor()  # FIXME: Configurable? Plugins?

        assert(self.app and self.search)

    def import_search(self, request_obj, initial_tags, force=False,
            callback_chain=[]):
        return self.call('import_search',
            request_obj, initial_tags, bool(force), callback_chain)

    def api_import_search(self,
            request, initial_tags, force, callback_chain, **kwargs):
        request_obj = to_jmap_request(request)
        def background_import_search():
            rv = self._import_search(request_obj, initial_tags, force,
                    callback_chain=callback_chain)
        self.add_background_job(background_import_search)
        self.reply_json({'running': True})

    def _get_email(self, metadata):
        try:
            if metadata.pointers[0].is_local_file:
                return self.fs.email(metadata, text=True, data=False)
            else:
                return self.app.jmap(RequestEmail(metadata=metadata, text=True)).get('email')
        except:
            return None

    def _index_full_messages(self, email_idxs, filters, callback_chain):
        # Full indexing, per message in "in:incoming":
        #
        #...
        email_idxs = list(self.search.intersect('in:incoming', email_idxs))
        print('FIXME: Should index emails %s' % email_idxs)
        keywords = {}
        for md in self.metadata.metadata(email_idxs):
             # 1. Submit a request to the main app to fetch the e-mail's
             #    text parts and structure (not full attachments). Again,
             #    we don't know or care where the mail is coming from.
             email = self._get_email(md)
             if not email:
                 continue

             # 2. Generate keywords and tags
             stat, kws = self.kwe.extract_email_keywords(md, email)
             # FIXME: Check status: want more data? e.g. full attachments?

             # 3. Run the filtering logic to mutate keywords/tags
             filters.filter(kws, md, email)
             keywords[md.idx] = list(kws)

             self.imported += 1
             if not self.keep_running:
                 return

        # 4. Add/remove results from the search engine
        self.search.add_results(
            list(keywords.items()), wait=False)

        # 5. Remove messages from Incoming
        # FIXME: Make this a callback action when add_results completes
        self.search.del_results(
            [[list(keywords.keys()), 'in:incoming']], wait=False)
        if self.imported > self.compacted + self.COMPACT_INTERVAL:
            self.search.compact(full=False)
            self.compacted = self.imported

        # 6. Report progress
        # FIXME: Make this a callback action when del_results completes
        if callback_chain:
            self.results_to_callback_chain(
                callback_chain, {'indexed': len(keywords)})

    def _import_search(self, request_obj, initial_tags, force,
            callback_chain=None):

        filters = FilterEngine().validate()  # FIXME: Take script as argument

        def _full_indexer(email_idxs):
            def _full_index():
                self._index_full_messages(email_idxs, filters, callback_chain)
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

            # 2. Add messages to metadata index, forward any new ones to the
            #    search engine for initial tagging (in:incoming, namespaces).
            idx_ids = self.metadata.add_metadata(emails, update=True)
            new_msgs = idx_ids['added']
            if force:
                new_msgs.extend(idx_ids['updated'])
            if new_msgs:
                added = self.search.add_results([[new_msgs, tags]])

                # 3. When search engine reports success, schedule full indexing
                #    and filtering of that batch of messages. We could do all
                #    at once, but this way we can report progress.
                self.add_background_job(_full_indexer(new_msgs), which='full')

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

    logging.basicConfig(level=logging.DEBUG)

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

