import copy
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

    async def async_augment(self, loop, metadatas, threads=False):
        mds = [Metadata(*m) for m in metadatas]
        hits = dict(
            (md.get_raw_header('Message-Id'), i)
            for i, md in enumerate(mds))

        wanted = list(hits.keys())
        if threads:
            wanted.extend(h for h
                in (md.get_raw_header('In-Reply-To') for md in mds) if h)
        res = await self.async_call(loop, 'metadata',
            wanted, False, threads, False, self.SORT_NONE, 0, None)

        def _augment_with(md):
            msgid = md.get_raw_header('Message-Id')
            which = hits.get(msgid) if msgid else None
            if which is None:
                hits[msgid] = md
                return md
            else:
                omd = mds[which]
                md.more.update(omd.more)
                omd.thread_id = md.thread_id
                omd.parent_id = md.parent_id
                omd.more.update(md.more)
                omd.more['syn_idx'] = omd.idx
                omd[Metadata.OFS_IDX] = md.idx
                return omd

        if not threads:
            for md in (Metadata(*m) for m in res['metadata']):
                _augment_with(md)
            return mds

        # Step 1: Augment our metadata, since that may change IDs.
        #         This will inject our messages into the threads and adjust
        #         the 'hits' list to only include original messages.
        threads = res['metadata']
        threads_by_msgid = {}
        mds_by_msgid = {}
        for thread in threads:
            thread['hits'] = []
            for i, md in enumerate(Metadata(*m) for m in thread['messages']):
                md = thread['messages'][i] = _augment_with(md)
                msgid = md.get_raw_header('Message-Id')
                if msgid:
                    mds_by_msgid[msgid] = md
                    threads_by_msgid[msgid] = thread
                if 'syn_idx' in md.more:
                    thread['hits'].append(md.idx)

        # Step 2: Iterate through our metadata, converting each message
        #         into its own one-message thread, or merging it into an
        #         existing thread if we have one.
        for md in sorted(mds):
            msgid = md.get_raw_header('Message-Id')
            parid = md.get_raw_header('In-Reply-To')
            mthread = threads_by_msgid.get(msgid)
            pthread = threads_by_msgid.get(parid)
            if mthread is None:
                thread = pthread
                if thread:
                    thread['hits'].append(md.idx)
                    thread['messages'].append(md)
                    thread['messages'][0].more['thread'].append(md.idx)
                    pmd = mds_by_msgid[parid]
                    md.parent_id = pmd.idx
                    md.thread_id = pmd.thread_id
                else:
                    thread = mthread = {
                        'hits': [md.idx],
                        'thread': md.thread_id,
                        'messages': [md]}
                    md.more['thread'] = [md.idx]
                    threads.append(thread)
                if msgid:
                    mds_by_msgid[msgid] = md
                    threads_by_msgid[msgid] = thread
            elif pthread and mthread != pthread:
                # Parent thread and message thread do not match, merge them!
                pthread_head = pthread['messages'][0]
                pthread['hits'].extend(mthread['hits'])
                pthread['messages'].extend(mthread['messages'])
                mthread['messages'][0].parent_id = mds_by_msgid[parid].idx
                for md in mthread['messages']:
                    md.thread_id = pthread_head.thread_id
                    pthread_head.more['thread'].append(md.idx)
                threads.remove(mthread)

        # Step 3: Sort our threads in ascending date order
        def _get_thread_ts(thread):
            try:
                return min(md.timestamp
                    for md in thread['messages'] if md.idx in thread['hits'])
            except ValueError:
                pass
            return md.timestamp
        threads.sort(key=_get_thread_ts)

        return threads

    async def async_metadata(self, loop, hits,
            tags=None, threads=False, only_ids=False,
            sort=SORT_NONE, skip=0, limit=None, raw=False,
            data_cb=None):
        res = await self.async_call(loop, 'metadata',
            hits, tags, threads, only_ids, sort, skip, limit,
            data_cb=data_cb)
        if only_ids or raw or (data_cb is not None):
            return res
        if threads:
            for grp in res['metadata']:
                grp['messages'] = [Metadata(*m) for m in grp['messages']]
        else:
            res['metadata'] = (Metadata(*m) for m in res['metadata'])
        return res

    def metadata(self, hits,
            tags=None, threads=False, only_ids=False,
            sort=SORT_NONE, skip=0, limit=None, raw=False):
        res = self.call('metadata',
            hits, tags, threads, only_ids, sort, skip, limit)
        if only_ids or raw:
            return res
        if threads:
            for grp in res:
                grp['messages'] = [Metadata(*m) for m in grp['messages']]
        else:
            res['metadata'] = (Metadata(*m) for m in res['metadata'])
        return res

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
            hits = dumb_decode(hits)
        if isinstance(hits, list):
            for i, h in enumerate(hits):
                try:
                    hits[i] = self._metadata.key_to_index(h)
                except KeyError:
                    pass
            hits = list(set([h for h in hits if isinstance(h, int)]))
        else:
            hits = list(hits)

        if not hits:
            return self.reply_json({'total': 0, 'metadata': []})

        if threads:
            result = self._md_threaded(hits, only_ids, sort_order)
        else:
            result = self._md_messages(hits, only_ids, sort_order)

        total = len(result)
        if not limit:
            limit = total - skip
        result = [r for r in result[skip:skip+limit]]

        if tags:
            for tag in tags:
                tags[tag] = dumb_decode(tags[tag][1])
            def _metadata(i):
                md = self._metadata.get(i, default=None)
                if md is None:
                    return None
                md.more['tags'] = tlist = []
                for tag in tags:
                    if i in tags[tag]:
                        tlist.append(tag)
                return md
        else:
            def _metadata(i):
                md = self._metadata.get(i, default=None)
                if md is None:
                    return None
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
                    grp['messages'] = [idx
                        for idx in (_metadata(i) for i
                            in self._metadata.get_thread_idxs(grp['thread']))
                        if idx is not None]
        elif not only_ids:
            result = (idx
                for idx in (_metadata(i) for i in result)
                if idx is not None)

        self.reply_json({
            'skip': skip,
            'limit': limit,
            'total': total,
            'metadata': list(result)})


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
            assert(msgid == m1[0].get_raw_header('Message-ID'))

            iset = dumb_encode_asc(IntSet([md_id]))
            m2 = list(mw.metadata(iset, sort=mw.SORT_DATE_ASC))
            assert(msgid == m2[0].get_raw_header('Message-ID'))

            if 'wait' not in sys.argv[1:]:
                mw.quit()
                print('** Tests passed, exiting... **')
            else:
                print('** Tests passed, waiting... **')

            mw.join()
        finally:
            mw.terminate()
