import json
import time
import traceback
import threading

from ..util.dumbcode import dumb_encode_asc
from .base import BaseWorker


class ImportWorker(BaseWorker):
    """
    """

    KIND = 'import'
    NICE = 20

    BATCH_SIZE = 100

    def __init__(self, status_dir,
            name=KIND, defaults=None):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'import_mailbox':  (True, self.api_import_mailbox),
            b'import_messages': (True, self.api_import_messages)})

        self._import_age = 0
        self._import_batch = []

    def import_mailbox(self, path_to_mailbox, callback_chain=[]):
        return self.call('import_mailbox', path_to_mailbox, callback_chain)

    def import_messages(self, list_of_metadata, callback_chain=[]):
        return self.call('import_message', message_data, callback_chain)

    def api_import_mailbox(self, path_to_mailbox, callback_chain):
        if not callback_chain:
            self.reply_json(self._import_mailbox(path_to_mailbox, None))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                rv = self._import_mailbox(path_to_mailbox, callback_chain)
            self.add_background_job(background_add_results)

    def api_import_messages(self, list_of_metadata, callback_chain):
        if not callback_chain:
            self.reply_json(self._import_messages(list_of_metadata, None))
        else:
            self.reply_json({'running': True})
            def background_add_results():
                rv = self._import_messages(list_of_metadata, callback_chain)
                self.results_to_callback_chain(rv, qs)
            self.add_background_job(background_add_results)

    def _import_mailbox(self, path_to_mailbox, callback_chain):
        # Generate metadata for all messages in mailbox, invoke _import_messages
        return {}

    def _import_messages(self, ids_and_metadata, callback_chain, force=False):
        # Import algorithm:
        #   1. If not forcing, ask search index which already imported, remove
        #   2. For each batch of N messages:
        #       1. Parse and collect keywords for all in batch
        #          -- This requires using metadata ptr to load actual message,
        #             is that also an async chain?
        #       2. Send keywords, ids, message metadata to filters/filter
        #       3. Chain from filters/filter to search/add_results
        #          -- filters could legit opt out of adding to search engine!
        #       4. Chain from search/add_results to import/done
        #       5. In import/done, tag the messages as fully imported
        #   3. When all batches are complete, invoke next step in our chain
        return {}
        #self.results_to_callback_chain(rv, qs)

# HMM, looks like chains need error endpoints as well.


if __name__ == '__main__':
    iw = ImportWorker('/tmp', name='moggie-imp-test').connect()
    if iw:
        print('URL: %s' % iw.url)
        try:
            print('** Tests passed, waiting... **')
            iw.join()
        finally:
            iw.terminate()

