import copy
import json
import logging
import os
import time
import traceback
import threading

if __name__ == '__main__':
    from .. import sys_path_helper

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

    @classmethod
    def Connect(cls, status_dir):
        return cls(status_dir, None, None).connect(autostart=False)

    def __init__(self, status_dir, metadata_dir, encryption_keys,
            name=KIND, notify=None, log_level=logging.ERROR):

        BaseWorker.__init__(self, status_dir,
            name=name, notify=notify, log_level=log_level)
        self.functions.update({
            b'info':         (True, self.api_info),
            b'compact':      (True, self.api_compact),
            b'add_metadata': (True, self.api_add_metadata),
            b'metadata':     (True, self.api_metadata)})

        self.change_lock = threading.Lock()
        self.encryption_keys = encryption_keys
        self.metadata_dir = metadata_dir
        self._metadata = None

    def quit(self, *args, **kwargs):
        with self.change_lock:
            super().quit(*args, **kwargs)

    def _main_httpd_loop(self):
        from ..storage.metadata import MetadataStore
        self._metadata = MetadataStore(
            os.path.join(self.metadata_dir, self.name),
            'metadata',
            self.encryption_keys)
        del self.encryption_keys
        return super()._main_httpd_loop()

    def compact(self, full=False, callback_chain=None):
        return self.call('compact', full, callback_chain)

    def add_metadata(self, metadata, update=True):
        return self.call('add_metadata', update, metadata)

    def metadata(self, hits,
            tags=None,
            threads=False,
            only_ids=False,
            sort=SORT_NONE,
            skip=0,
            limit=None,
            raw=False):
        res = self.call('metadata',
            hits, tags, threads, only_ids, sort, skip, limit)
        if only_ids or raw:
            return res
        if threads:
            for grp in res:
                grp['messages'] = [Metadata(*m) for m in grp['messages']]
            return res
        else:
            return (Metadata(*m) for m in res)

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

    def _md_threaded(self, hits, only_ids, sort_order):
        hits = [self._metadata.thread_sorting_keyfunc(h) for h in hits]
        hits.sort()
        if sort_order == self.SORT_DATE_DEC:
            hits.reverse()

        groups = []
        last_tid = 0
        for tid, ts, idx in hits:
            if tid != last_tid:
                groups.append({'hits': [idx], '_ts': ts, 'thread': tid})
                last_tid = tid
            else:
                groups[-1]['_ts'] = min(groups[-1]['_ts'], ts)
                groups[-1]['hits'].append(idx)

        if sort_order != self.SORT_NONE:
            groups.sort(key=lambda g: g['_ts'])
        if sort_order == self.SORT_DATE_DEC:
            groups.reverse()

        return groups

    def _md_messages(self, hits, only_ids, sort_order):
        if sort_order != self.SORT_NONE:
            hits.sort(key=self._metadata.date_sorting_keyfunc)
        if sort_order == self.SORT_DATE_DEC:
            hits.reverse()

        return hits

    def api_metadata(self,
            hits, tags, threads, only_ids, sort_order, skip, limit,
            **kwargs):
        if not isinstance(hits, (list, IntSet)):
            hits = list(dumb_decode(hits))
        if not hits:
            return self.reply_json([])

        if threads:
            result = self._md_threaded(hits, only_ids, sort_order)
        else:
            result = self._md_messages(hits, only_ids, sort_order)

        if not limit:
            limit = len(result) - skip
        result = [r for r in result[skip:skip+limit]]

        if tags:
            for tag in tags:
                tags[tag] = dumb_decode(tags[tag][1])
            def _metadata(i):
                md = self._metadata.get(i, default=i)
                md.more['tags'] = tlist = []
                for tag in tags:
                    if i in tags[tag]:
                        tlist.append(tag)
                return md
        else:
            def _metadata(i):
                md = self._metadata.get(i, default=i)
                if 'tags' in md.more:
                    del md.more['tags']
                return md

        if threads:
            if only_ids:
                for grp in result:
                    del grp['_ts']
                    tid = grp['thread']
                    grp['messages'] = self._metadata.get_thread_idxs(tid)
            else:
                for grp in result:
                    del grp['_ts']
                    grp['messages'] = [_metadata(i)
                        for i in self._metadata.get_thread_idxs(grp['thread'])]
        elif not only_ids:
            result = [_metadata(i) for i in result]

        self.reply_json(result)


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
