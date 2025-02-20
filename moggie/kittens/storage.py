# This is take two of a storage worker pool. This time using kettlingar!
#
# All backends are capable of all things, but the StorageTriageKitten is
# in charge of assigning responsibilities.
#
import asyncio
import base64
import os
import time
import traceback

from kettlingar import RPCKitten

from ..email.metadata import Metadata
from ..email.sync import generate_sync_id
from ..storage.files import FileStorage
from ..storage.imap import ImapStorage
from ..util.mailpile import PleaseUnlockError
from .common import MoggieKitten


class StorageBackendKitten(MoggieKitten):
    BLOCK_BYTES = 1024 * 128

    class Configuration(MoggieKitten.Configuration):
        WORKER_NAME = 'storage_worker'

    async def init_servers(self, servers):
        self.imap = self.fs = self.metadata = None
        self.message_cache = {}
        return servers

    def get_backend(self, key):
        # FIXME
        if key.startswith('imap:'):
            if not self.imap:
                self.imap = ImapStorage(metadata=self.metadata)
            return self.imap

        if not self.fs:
            self.fs = FileStorage(
                metadata=self.metadata,
                relative_to=os.path.expanduser('~'))
        return self.fs

    async def api_key_get(self, request_info, key,
            begin=0,
            end=None,
            block_size=None, 
            json_safe=False):
        """/key_get <key> [options]

        Fetch the data represented by `key`, returning it as binary.

        Options:
            --begin=<N>       Return data from offset N onwards
            --end=<N>         Return data preceding offset N
            --json-safe=True  Base64 encode binary data in a JSON object
            --block-size=<N>  Read/return data in chunks of max <N> bytes

        Note that `block-size`, `begin` and `end` all reference pre-encoding
        sizes. Expect more bytes on the wire when encoding for JSON (base64).
        """
        block_size = int(block_size or self.BLOCK_BYTES)
        json_safe = self.Bool(json_safe)
        begin = int(begin)
        end = (int(end) + 1) if end else end
        t0 = time.time()
        try:
            # FIXME: On IMAP this will always fetch (and cache) the entire
            #        message, even if we've requested a range.
            value = self.get_backend(key)[key]
            if value is None:
                raise KeyError('Not a normal file')

        except KeyError as ke:
            yield None, self.progress(
                'Resource unavailable: %(key)s (%(error)s)',
                key=key,
                finished=404,
                error=str(ke))
            return

        except PleaseUnlockError as pue:
            yield None, self.progress(
                'Resource is locked: %(key)s (%(error)s)',
                key=key,
                finished=401,
                error=str(pue))
            return

        if json_safe:
            def _fmt(b, e, chunk_data):
                return None, {
                    'data': str(base64.b64encode(chunk_data), 'latin-1'),
                    'begin': p,
                    'end': e - 1}
        else:
            def _fmt(b, e, chunk_data):
                return 'application/octet-stream', chunk_data

        length = len(value or '')
        length = min(length, end if end else length)
        length -= begin

        p = begin
        for chunk in range(0, 1 + length // block_size):
            chunk_end = min(p+block_size, begin+length)
            if p < chunk_end:
                chunk_data = value[p:chunk_end]
                yield _fmt(p, chunk_end, chunk_data)
            p += block_size

        # Generate this even if we don't return it; logging is a side-effect
        progress = self.progress(
            'Fetched %(length)s bytes in %(elapsed_ms)sms',
            finished=True,
            elapsed_ms=int((time.time() - t0) * 1000),
            length=length)
        if json_safe:
            yield None, progress

    async def api_key_info(self, request_info, key,
            details=None, recurse=0, relpath=None,
            username=None, password=None):
        """/key_info <key> [options]

        This function is a generator, which will yield one dictionary
        of information per file or directory found at the requested path.

        Options:
            --details=True   Include more details about each match
            --recurse=<N>    Recurse up to N levels deep into the tree
        """
        recurse = int(recurse)
        details = self.Bool(details)
        try:
            # FIXME: Change the storage backend into a generator?
            pending = [self.get_backend(key).info(key,
                details=details,
                recurse=recurse,
                relpath=relpath,
                username=username,
                password=password)]

            while pending:
                result = pending.pop(0)
                pending.extend(result.pop('contents', []))
                yield None, result

        except PleaseUnlockError as pue:
            yield None, self.progress(
                'Resource is locked: %(key)s (%(error)s)',
                key=key,
                finished=401,
                error=str(pue))

        except KeyError as ke:
            yield None, self.progress(
                'Resource unavailable: %(key)s (%(error)s)',
                key=key,
                finished=404,
                error=str(pue))

    def _prep_filter(self, terms):
        if not terms:
            return (lambda t: t, None)

        # FIXME: This is very primitive and ignores most of the syntax
        #        from the proper search engine. But it kinda works?
        terms = (terms or '').replace('(', '').replace(')', '').split()
        ids = [int(i[3:]) for i in terms if i[:3] == 'id:']
        terms = set([t.lower() for t in terms if t[:3] not in ('id:', 'OR')])

        if not terms:
            return (lambda t: t, ids)

        from moggie.search.extractor import KeywordExtractor
        kwe = KeywordExtractor()

        def msg_terms(r):
            r = Metadata(*r)

            # Check for substring matches within selected headers
            rt = set()
            for term in terms:
                if term in r.headers.lower():
                    rt.add(term)

            # Generate the same keywords as the search index uses
            rt |= kwe.header_keywords(r, r.parsed())[1]
            return rt

        def _filter(msg):
            return not (terms - msg_terms(msg))

        return (_filter, ids)

    async def api_mailbox(self, request_info, key,
            terms=None, skip=0, limit=None, reverse=False,
            username=None, password=None,
            sync_src=None, sync_dest=None):
        """/mailbox <key> [options]

        This function is a generator, which will yield one dictionary of
        message metadata per message found in the requested mailbox.

        Options:
            --terms=<T>     Only return messages matching these search terms
            --skip=<N>      Skip first N results (default=0)
            --limit=<N>     Return at most N results (default is no limit)
            --reverse=<Y>   Process mailbox in reverse order

            --username=<U>  Username to use for accessing mailbox
            --password=<U>  Username to use for accessing mailbox

            --sync-src=<S>  Generate sync IDs using this source
            --sync-dest=<D> Generate sync IDs using this destination
        """
        skip = int(skip)
        limit = int(limit) if limit else None
        reverse = self.Bool(reverse)

        unique_app_id = await self.unique_app_id()
        sync_dest = sync_dest or key
        sync_id = generate_sync_id(unique_app_id, sync_src, sync_dest)
        yield None, self.progress(
            'Sync ID is %(sync_id)s (unique_app_id=%(unique_app_id)s,' +
            ' src=%(sync_src)s, dest=%(sync_dest)s)',
            unique_app_id=unique_app_id,
            sync_src=sync_dest,
            sync_dest=sync_dest,
            sync_id=sync_id)

        _filter, wanted_ids = self._prep_filter(terms)

        t0 = time.time()
        count = 0
        try:
            parser = self.get_backend(key).iter_mailbox(key,
                ids=(wanted_ids or None),
                skip=skip,
                reverse=reverse,
                sync_id=sync_id,
                # FIXME: Pass terms to the backend to enable server-side search
                # FIXME: Pass progress function to backend, for updates!
                username=username, password=password)

            for msg in parser:
                if self.IsProgress(msg):
                    yield None, msg
                elif _filter(msg):
                    yield None, msg
                    count += 1
                    if limit is not None:
                        limit -= 1
                        if not limit:
                            break

            yield None, self.progress(
                'Found %(count)s emails in %(elapsed_ms)sms',
                finished=True,
                elapsed_ms=int((time.time() - t0) * 1000),
                count=count)

        except PleaseUnlockError as pue:
            yield None, self.progress(
                'Resource is locked: %(key)s (%(error)s)',
                finished=401,
                elapsed_ms=int((time.time() - t0) * 1000),
                key=key,
                error=str(pue))

        except Exception as exc:
            yield None, self.progress(
                'Aborted! Error: %(error)s',
                finished=500,
                elapsed_ms=int((time.time() - t0) * 1000),
                error=str(exc),
                traceback=traceback.format_exc())

    async def api_email(self, request_info, metadata,
            text=False, data=False, full_raw=False, parts=None,
            username=None, password=None):
        """/email <metadata> [<options>]
 
        FIXME
        """
        yield None, {'FIXME': True}

    async def api_delete_emails(self, request_info, mailbox,  metadata_list,
            username=None, password=None):
        """/delete_emails <mailbox> <metadata_list> [<options>]
 
        FIXME
        """
        yield None, {'FIXME': True}


class StorageTriageKitten(MoggieKitten):
    """moggie.kittens.storage.StorageTriageKitten

    This class implements a microservice for storage triage. This
    service takes care of starting/stopping storage workers, choosing
    a worker for each job and forwarding requests onwards.

    TODO: We should implement backend-only copy operations, and then
          when a copy is requested make sure the work happens in a
          backend responsible for both the source and destination tree.

    TODO: Implement client-side short-cuts for filesystem read ops?
    """
    class Configuration(MoggieKitten.Configuration):
        WORKER_NAME = 'storage_triage'

    DOC_STRING_MAP = {
        'api_unique_app_id': MoggieKitten.api_unique_app_id.__doc__,
        'raw_key_get': StorageBackendKitten.api_key_get.__doc__,
        'raw_key_info': StorageBackendKitten.api_key_info.__doc__,
        'raw_mailbox': StorageBackendKitten.api_mailbox.__doc__}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def init_servers(self, servers):
        self.lock = asyncio.Lock()
        async with self.lock:
            self.paths = {}
            self.backends = {}
            self.count = 0
            self.create_task(self._add_backend())

        return servers

    async def shutdown(self):
        for name, backend in self.backends.items():
            try:
                await backend.quitquitquit()
            except ConnectionRefusedError:
                self.debug('Not running %s' % name)
            except Exception as e:
                self.exception('Failed to shut down %s' % name)

    async def _add_backend(self):
        self.count += 1
        if self.count > 1:
            name = 'storage_worker_%d' % self.count
        else:
            name = 'storage_worker'

        async with self.lock:
            unique_app_id = await self.unique_app_id()
            backend = StorageBackendKitten(args=[
                '--app-name=%s' % self.config.app_name,
                '--worker-name=%s' % name])
            try:
                be = self.backends[name] = await backend.connect(auto_start=True)
                if unique_app_id:
                    await be.unique_app_id(set_id=unique_app_id)
            except RPCKitten.NotRunning:
                return False

            return backend

    def _choose_backend(self, key):
        # No need for a lock, this a sync function
        for name, backend in self.backends.items():
            return backend

    async def api_unique_app_id(self, request_info, set_id=None):
        if set_id is None:
            return await super().api_unique_app_id(request_info)
        async with self.lock:
            for backend in self.backends.values():
                await backend.unique_app_id(set_id=set_id)
            return await super().api_unique_app_id(request_info, set_id=set_id)

    async def raw_key_get(self, request_info, key,
            begin=0,
            end=None,
            block_size=None, 
            json_safe=False):
        async for res in self._choose_backend(key).key_get(key,
                begin=begin,
                end=end,
                json_safe=json_safe,
                block_size=block_size,
                call_reply_to=request_info):
            if not res.get('replied_to_first_fd'):
                raise RuntimeError('Delegating reply to backend failed')

    async def raw_key_info(self, request_info, key,
            details=None, recurse=0, relpath=None,
            username=None, password=None):
        async for res in self._choose_backend(key).key_info(key,
                details=details,
                recurse=recurse,
                relpath=relpath,
                username=username,
                password=password,
                call_reply_to=request_info):
            if not res.get('replied_to_first_fd'):
                raise RuntimeError('Delegating reply to backend failed')

    async def raw_mailbox(self, request_info, key,
            terms=None, skip=0, limit=None, reverse=False,
            username=None, password=None,
            sync_src=None, sync_dest=None):
        async for res in self._choose_backend(key).mailbox(key,
                terms=terms, skip=skip, limit=limit, reverse=reverse,
                username=username, password=password,
                sync_src=sync_src, sync_dest=sync_dest,
                call_reply_to=request_info):
            if not res.get('replied_to_first_fd'):
                raise RuntimeError('Delegating reply to backend failed')


if __name__ == '__main__':
    import sys
    args = list(sys.argv[1:])
    if '--worker' in args:
        args.remove('--worker')
        StorageBackendKitten.Main(args)
    else:
        StorageTriageKitten.Main(args)
