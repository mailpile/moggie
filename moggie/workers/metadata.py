import json
import logging
import os
import time
import traceback
import threading

from ..email.metadata import Metadata
from ..util.dumbcode import dumb_encode_asc, dumb_decode
from ..util.intset import IntSet
from .base import BaseWorker


class MetadataWorker(BaseWorker):
    """
    """
    KIND = 'metadata'

    SORT_NONE = 0
    SORT_DATE_ASC = 1
    SORT_DATE_DEC = 2

    def __init__(self, status_dir, metadata_dir, encryption_keys, name=KIND):

        BaseWorker.__init__(self, status_dir, name=name)
        self.functions.update({
            b'info':         (True, self.api_info),
            b'compact':      (True, self.api_compact),
            b'add_metadata': (True, self.api_add_metadata),
            b'metadata':     (True, self.api_metadata)})

        self.change_lock = threading.Lock()
        self.encryption_keys = encryption_keys
        self.metadata_dir = metadata_dir
        self._metadata = None

    def _main_httpd_loop(self):
        from ..storage.metadata import MetadataStore
        self._metadata = MetadataStore(
            os.path.join(self.metadata_dir, self.name),
            'metadata',
            self.encryption_keys)
        del self.encryption_keys
        return super()._main_httpd_loop()

    def compact(self, compact, full=False):
        return self.call('compact', full)

    def add_metadata(self, metadata, update=True):
        return self.call('add_metadata', update, metadata)

    def metadata(self, hits, sort=SORT_NONE, skip=0, limit=None):
        return (Metadata(*m) for m in
            self.call('metadata', hits, sort, skip, limit))

    def info(self):
        return self.call('info')

    def api_info(self, **kwas):
        self.reply_json({
            'maxint': len(self._metadata)})

    def api_compact(self, full, callback_chain, **kwargs):
        def background_compact():
            with self.change_lock:
                self._metadata.compact(partial=not full)
                self.results_to_callback_chain(callback_chain,
                    {'compacted': True, 'full': full})
        self.add_background_job(background_compact)
        self.reply_json({'running': True})

    def api_add_metadata(self, update, metadata, **kwas):
        added, updated = [], []
        for m in sorted(metadata):
            if isinstance(m, list):
                m = Metadata(*m)
            with self.change_lock:
                if update:
                    is_new, idx = self._metadata.update_or_add(m)
                else:
                    is_new = False
                    idx = self._metadata.add_if_new(m)
            if idx:
                if is_new:
                    added.append(idx)
                else:
                    updated.append(idx)
        self.reply_json({'added': added, 'updated': updated})

    def api_metadata(self, hits, sort_order, skip, limit, **kwargs):
        if not isinstance(hits, (list, IntSet)):
            hits = list(dumb_decode(hits))
        if sort_order == self.SORT_DATE_ASC:
            hits.sort(key=self._metadata.date_sorting_keyfunc)
        elif sort_order == self.SORT_DATE_DEC:
            hits.sort(key=self._metadata.date_sorting_keyfunc)
            hits.reverse()
        if not limit:
            limit = len(hits) - skip
        self.reply_json([r for r in (
            self._metadata.get(i, default=None) for i in hits[skip:skip+limit]
            ) if r is not None])


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.DEBUG)
    os.system('rm -rf /tmp/moggie-md-test')
    mw = MetadataWorker('/tmp', '/tmp', [b'1234'], name='moggie-md-test').connect()
    if mw:
        print('URL: %s' % mw.url)
        msgid = '<this-is-a-ghost@moggie>'
        try:
            added = mw.add_metadata([Metadata.ghost(msgid)])
            assert(len(added['updated']) == 0)
            assert(len(added['added']) == 1)
            md_id = added['added'][0]

            m1 = list(mw.metadata([md_id], sort=mw.SORT_DATE_ASC))
            assert(msgid == m1[0].get_raw_header('Message-Id'))

            iset = dumb_encode_asc(IntSet([md_id]))
            m2 = list(mw.metadata(iset, sort=mw.SORT_DATE_ASC))
            assert(msgid == m2[0].get_raw_header('Message-Id'))

            if 'wait' not in sys.argv[1:]:
                mw.quit()
                print('** Tests passed, exiting... **')
            else:
                print('** Tests passed, waiting... **')

            mw.join()
        finally:
            mw.terminate()
