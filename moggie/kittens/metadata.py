import asyncio
import base64
import copy
import logging

if __name__ == '__main__':
    from .. import sys_path_helper

from ..email.metadata import Metadata
from ..util.dumbcode import dumb_encode_asc, dumb_decode
from ..util.intset import IntSet
from .common import MoggieKitten


class MetadataKitten(MoggieKitten):
    """moggie.kittens.metadata.MetadataKitten

    This class implements a microservice which manages the moggie
    metadata store.
    """
    class Configuration(MoggieKitten.Configuration):
        WORKER_NAME = 'metadata'

    SORT_NONE = 0
    SORT_DATE_ASC = 1
    SORT_DATE_DEC = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._change_lock = None
        self._metadata_dir = None
        self._metadata = None

    def _please_unlock(self, _raise=False):
        err = 'Please unlock the metadata store first'
        progress = self.progress(err, finished=401, error=err)
        if _raise:
            raise PermissionError(err)
        else:
            return progress

    async def api_unlock(self, request_info, metadata_dir, encryption_keys):
        """/unlock <metadata_dir> <encryption_keys>

        Unlock the metadata store at `<metadata_dir>` (a filesystem path)
        using the provided encryption keys (a list of AES keys).

        (For use on the CLI, `<encryption_keys>` may be a comman-separated
        list of base64 encoded keys.)
        """
        from ..storage.metadata import MetadataStore

        # Make it possible to use/test this function from the command line.
        if isinstance(encryption_keys, str):
            encryption_keys = [
                base64.b64decode(k.strip())
                for k in encryption_keys.split(',')]

        if self._change_lock is None:
            self._change_lock = asyncio.Lock()
        async with self._change_lock:
            self._metadata = MetadataStore(
                metadata_dir, 'metadata', encryption_keys)
            self._metadata_dir = metadata_dir

        return await self.api_info(request_info)

    async def api_info(self, request_info):
        """/info

        Return information about the currently active/unlocked metadata index.
        """
        if self._metadata is None:
            self._please_unlock(_raise=True)
        return None, {
            'metadata_dir': self._metadata_dir,
            'maxint': len(self._metadata)}

    async def api_compact(self, request_info, full=False):
        """/compact [<options>]

        Compact the metadata index. Note that metadata will be unavailable
        for writes while this runs.

        Options:
            --full=<F>  If F is true, force a full compaction.

        Returns:
            This function is a generator which yields progress objects.
        """
        full = self.Bool(full)
        if self._metadata is None:
            yield None, self._please_unlock()
            return

        yield None, self.progress(
            'Compacting the metadata index (full=%(full)s) ...',
            full=full)

        async with self._change_lock:
            for progress in self._metadata.compact(partial=not full):
                yield None, self.progress('Compacting', **progress)
                await asyncio.sleep(0)  # Let other things run too

        yield None, self.progress(
            'Finished compacting the metadata index (full=%(full)s).',
            finished=True,
            full=full)

    async def api_add_metadata(self, request_info, metadata_list, update=False):
        """/add_metadata <metadata_list> [<options>]

        Add metadata entries to the index.

        Options:
            --update=<U>  If U is True, allow updates to existing metadata

        Returns:
            This function is a generator which yields progress objects.

        The final progress object will include lists of which IDs were added
        to the index, and which were updated. It will also include an `ids`
        element, which maps input metadata IDs (or path pointers) to the IDs
        assigned by the metadata index.
        """
        if self._metadata is None:
            yield None, self._please_unlock()
            return

        update = self.Bool(update)
        if update:
            fmt1 = ('Updating metadata index: %(percent)s%% ' +
                    '(added=%(added_count)s, updated=%(updated_count)s) ...')
            fmt2 = ('Updated metadata index, ' +
                    'added %(added_count)s, updated %(updated_count)s.')
        else:
            fmt1 = ('Adding to metadata index: %(percent)s%% ' +
                    '(added=%(added_count)s, skipped=%(skipped_count)s) ...')
            fmt2 = ('Added to metadata index, ' +
                    'added %(added_count)s, skipped %(skipped_count)s.')

        total = len(metadata_list)
        added, updated, id_map = [], [], {}
        for count, m in enumerate(sorted(metadata_list)):
            if isinstance(m, list):
                m = Metadata(*m)

            async with self._change_lock:
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
                if m.idx:
                    id_map[str(m.idx)] = idx
                else:
                    ptrs = m.pointers
                    if ptrs:
                         id_map[ptrs[0].ptr_path] = idx

            if not count % 123:
                yield None, self.progress(fmt1,
                    percent=(100*count) // total,
                    added_count=len(added),
                    updated_count=len(updated),
                    skipped_count=count - len(added) - len(updated))

            elif not count % 16:
                await asyncio.sleep(0)  # Let other things run too

        yield None, self.progress(fmt2,
             finished=True,
             added=list(added),
             added_count=len(added),
             updated=list(updated),
             updated_count=len(updated),
             skipped_count=count - len(added) - len(updated),
             ids=id_map)

    async def api_annotate(self, request_info, msgids, annotations):
        """/annotate <msgids> <annotations>

        Add/remove a set of annotations to a list of messages.

        Arguments:
            msgids: [msgid1, msgid2, ...]
            annotations: {key: value, ...}

        Returns:
            A list of updated message IDs.

        If an annotation is set to an empty or None value, that annotation is
        deleted from metadata. Annotations keys will be normalized so they are
        lower-case and start with a '=' character.
        """
        if self._metadata is None:
            self._please_unlock(_raise=True)

        updated = []
        if not (msgids and annotations):
            return None, updated

        logging.debug('api_annotate(%s, %s)' % (msgids, annotations))

        for count, msgid in enumerate(msgids):
            async with self._change_lock:
                try:
                    md = self._metadata[msgid]
                except KeyError:
                    continue

                for key, val in annotations.items():
                    key = key.strip()
                    if not key:
                        continue
                    elif key[:1] != '=':
                        key = '=' + key

                    md.more[key] = val

                self._metadata.update_or_add(md)

            updated.append(msgid)
            if not count % 16:
                await asyncio.sleep(0)  # Let other things run too

        return None, updated

    async def api_update_ptrs(self, request_info, msgids_to_ptrs):
        """
        Update pointers in the metadata index.

        Arguments:
            msgids_to_ptrs: {msgid: [Metadata.PTR.1, PTR.2, ...], ...}

        Returns:
            A list of updated message IDs.

        Any old pointers within the same container will be replaced.
        If a list of pointers is empty, that implies the message is gone.
        """
        if self._metadata is None:
            self._please_unlock(_raise=True)

        updated = []
        for count, (msgid, pointers) in enumerate(msgids_to_ptrs.items()):
            async with self._change_lock:
                try:
                    metadata = self._metadata[msgid]
                except KeyError:
                    continue

                if metadata.add_pointers(pointers):
                    self._metadata.update_or_add(metadata)
                    updated.append(msgid)

            if not count % 16:
                await asyncio.sleep(0)  # Let other things run too

        return None, updated

    def _md_threaded(self, hits, only_ids, sort_order, urgent):
        hits = [self._metadata.thread_sorting_keyfunc(h) for h in hits]
        hits.sort()
        if sort_order == self.SORT_DATE_DEC:
            hits.reverse()

        groups = []
        last_tid = -1
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

        if urgent and (sort_order != self.SORT_NONE):
            return (
                [g for g in groups if g['thread'] in urgent] +
                [g for g in groups if g['thread'] not in urgent])

        return groups

    def _md_messages(self, hits, only_ids, sort_order, urgent):
        if sort_order != self.SORT_NONE:
            hits.sort(key=self._metadata.date_sorting_keyfunc)
        if sort_order == self.SORT_DATE_DEC:
            hits.reverse()

        if urgent and (sort_order != self.SORT_NONE):
            return (
                [h for h in hits if h in urgent] +
                [h for h in hits if h not in urgent])

        return hits

    async def api_metadata(self, request_info, hits,
            tags=None, threads=False, only_ids=False,
            sort=SORT_NONE, skip=0, limit=None):
        """/metadata <hits> [<options>]

        Fetch the metadata entries for a set of messages (hits).

        The result may include metadata about other related messages (e.g.
        thread siblings and parents). If tag information is included in the
        input, message metadata will include tag information and the sort
        order will prioritise messages tagged `in:urgent`.

        Options:
            --tags=<T>       ... FIXME ...
            --threads=<B>    If B is True, expand and return entire threads
            --only_ids=<B>   If B is True, only return metadata index IDs
            --sort=<O>       Sort by: 0=Unsorted, 1=Date descending, 2=Date asc
            --skip=<N>       Pagination: skip the first N results
            --limit=<N>      Pagination: return at most N results

        Returns:
            This function is a generator which yields progress objects,
            metadata objects or thread objects.
        """
        if self._metadata is None:
            yield None, self._please_unlock()
            return
        try:
            skip = int(skip)
            sort = int(sort)
            limit = int(limit) if limit else None
            threads = self.Bool(threads)
            only_ids = self.Bool(only_ids)

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
                yield None, {'total': 0}
                return

            urgent = (tags or {}).get('in:urgent')
            if urgent:
                urgent = dumb_decode(urgent[1])
            else:
                urgent = set()

            if threads:
                result = self._md_threaded(hits, only_ids, sort, urgent)
            else:
                result = self._md_messages(hits, only_ids, sort, urgent)

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

                
            progress_msg = 'Found %(total)s metadata entries'
            if threads:
                progress_msg = 'Found %(total)s threads'
                if only_ids:
                    def _expand_grp(grp):
                        del grp['_ts']
                        tid = grp['thread']
                        grp['messages'] = self._metadata.get_thread_idxs(tid)
                        return grp
                else:
                    def _expand_grp(grp):
                        del grp['_ts']
                        grp['messages'] = [idx
                            for idx in (_metadata(i) for i
                                in self._metadata.get_thread_idxs(grp['thread']))
                            if idx is not None]
                        return grp
                result = (_expand_grp(grp) for grp in result)

            elif not only_ids:
                result = (idx
                    for idx in (_metadata(i) for i in result)
                    if idx is not None)

            for r in result:
                yield None, r

            yield None, self.progress(progress_msg,
                finished=True,
                only_ids=only_ids,
                threads=threads,
                skip=skip,
                limit=limit,
                total=total)

        except Exception as e:
            import traceback
            yield None, self.progress(str(e),
                finished=500,
                error=str(e),
                traceback=traceback.format_exc())

    async def metadata(self, *args, **kwargs):
        """
        Custom convenience: ensure we return Metadata objects
        """
        if self.is_service:
            async for res in self.api_metadata(*args, **kwargs):
                yield res[1]
            return

        res_generator = (await self.call('metadata', *args, **kwargs))()

        if kwargs.get('only_ids') or kwargs.get('raw'):
            async for res in res_generator:
                yield res

        elif kwargs.get('threads'):
            async for res in res_generator:
                if not self.IsProgress(res):
                    res['messages'] = [Metadata(*m) for m in res['messages']]
                yield res

        else:
            async for res in res_generator:
                if not self.IsProgress(res):
                    res = Metadata(*res)
                yield res

    async def api_augment(self, request_info, metadatas,
            threads=False,
            only_indexed=False,
            only_unindexed=False):
        """/augment <metadata_list> [<options>]

        Augment existing metadata with information from the metadata index.
        This is primarily used when generating metadata directly from a
        mailbox, where some messages will be known to the index but not all.

        Returns:
            A modified list of metadata entries.
        """
        # NOTE: Do not check for self._metadata, as we may be running on the
        #       client. The check will happen in the metadata call below.

        threads = self.Bool(threads)
        only_indexed = self.Bool(only_indexed)
        only_unindexed = self.Bool(only_unindexed)

        mds = [Metadata(*m) for m in metadatas]
        input_idxs = set(md.idx for md in mds)
        hits = dict(
            (md.get_raw_header_str('Message-Id'), i)
            for i, md in enumerate(mds))

        wanted = list(hits.keys())
        if threads:
            wanted.extend(h for h
                in (md.get_raw_header_str('In-Reply-To') for md in mds) if h)

        res_generator = self.metadata(wanted, threads=threads)

        def _augment_with(md):
            msgid = md.get_raw_header_str('Message-Id')
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
                omd.more['metadata_idx'] = md.idx
                #omd[Metadata.OFS_IDX] = md.idx
                return omd

        def _wanted(md):
            syn_idx = md.more.get('syn_idx')
            if (md.idx not in input_idxs) and (syn_idx not in input_idxs):
                return False
            if only_unindexed and syn_idx:
                return False
            if only_indexed and not syn_idx:
                return False
            return True

        if not threads:
            async for md in res_generator:
                if not self.IsProgress(md):
                     md = Metadata(*md)
                     _augment_with(md)
            return None, [md for md in mds if _wanted(md)]

        # Step 1: Augment our metadata, since that may change IDs.
        #         This will inject our messages into the threads and adjust
        #         the 'hits' list to only include original messages.
        threads = []
        threads_by_msgid = {}
        mds_by_msgid = {}
        async for thread in res_generator:
            if self.IsProgress(thread):
                continue
            threads.append(thread)
            thread['hits'] = []
            for i, md in enumerate(Metadata(*m) for m in thread['messages']):
                md = thread['messages'][i] = _augment_with(md)
                msgid = md.get_raw_header_str('Message-Id')
                if msgid:
                    mds_by_msgid[msgid] = md
                    threads_by_msgid[msgid] = thread
                if _wanted(md):
                    thread['hits'].append(md.idx)

        # Step 2: Iterate through our metadata, converting each message
        #         into its own one-message thread, or merging it into an
        #         existing thread if we have one.
        for md in sorted(mds):
            msgid = md.get_raw_header_str('Message-Id')
            parid = md.get_raw_header_str('In-Reply-To')
            mthread = threads_by_msgid.get(msgid)
            pthread = threads_by_msgid.get(parid)
            if mthread is None:
                thread = pthread
                if thread:
                    if _wanted(md):
                        thread['hits'].append(md.idx)
                    thread['messages'].append(md)
                    try:
                        thread['messages'][0].more['thread'].append(md.idx)
                    except KeyError:
                        thread['messages'][0].more['thread'] = [md.idx]
                    pmd = mds_by_msgid[parid]
                    md.parent_id = pmd.idx
                    md.thread_id = pmd.thread_id
                else:
                    thread = mthread = {
                        'hits': [md.idx] if _wanted(md) else [],
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
                pthread['messages'].extend(mthread['messages'])
                mthread['messages'][0].parent_id = mds_by_msgid[parid].idx
                for md in mthread['messages']:
                    md.thread_id = pthread_head.thread_id
                    try:
                        pthread_head.more['thread'].append(md.idx)
                    except KeyError:
                        pthread_head.more['thread'] = [md.idx]
                pthread['hits'] = [md.idx for md in pthread['messages'] if _wanted(md)]
                try:
                    threads.remove(mthread)
                except ValueError:
                    pass

        # Step 3: Sort our threads in the order they appeared in the mailbox
        def _get_thread_rank(thread):
            try:
                return min(md.pointers[0].ptr_rank
                    for md in thread['messages'] if md.idx in thread['hits'])
            except ValueError:
                pass
            return md.timestamp
        threads.sort(key=_get_thread_rank)

        return None, [th for th in threads if th.get('hits')]

    async def augment(self, metadatas, **kwargs):
        """
        Custom convenience: run most of the logic client side.
        """
        return (await self.api_augment(None, metadatas, **kwargs))[1]


if __name__ == '__main__':
    import sys
    if '--test' not in sys.argv:
        MetadataKitten.Main(sys.argv[1:])

    else:
        import os
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
                assert(msgid == m1[0].get_raw_header_str('Message-ID'))

                iset = dumb_encode_asc(IntSet([md_id]))
                m2 = list(mw.metadata(iset, sort=mw.SORT_DATE_ASC))
                assert(msgid == m2[0].get_raw_header_str('Message-ID'))

                if 'wait' not in sys.argv[1:]:
                    mw.quit()
                    print('** Tests passed, exiting... **')
                else:
                    print('** Tests passed, waiting... **')

                mw.join()
            finally:
                mw.terminate()
