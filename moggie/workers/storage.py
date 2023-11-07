# OK, so IMAP and other remote resources are ALSO storage.
#
# It makes sense to triage between them here, but it also makes sense
# for that to be multiple workers in practice. Just one is asking for
# ops to block.
#
# In fact, we are already blocking on IO as is, when scanning large
# amounts of mail etc. It would be good to have a pool of workers here.
# We also need to contextualize access, since although any context could
# connect to any server - entering credentials in one shouldn't unlock
# for the others.
#
# To keep the layering overhead to a minimum, choosing backends and
# launching new ones as needed, should happen at the caller.
#
import logging
import os
import re
import time
import traceback
import threading

from ..api.exceptions import APIException, NeedInfoException
from ..storage.files import FileStorage
from ..storage.imap import ImapStorage
from ..util.dumbcode import *
from ..util.mailpile import PleaseUnlockError
from ..email.metadata import Metadata

from .base import BaseWorker, WorkerPool


class StorageWorkerApi:
    async def async_info(self, loop,
            key=None, details=None, recurse=0, relpath=None,
            username=None, password=None):
        return await self.async_call(loop, 'info',
            key, details, recurse, relpath, username, password)

    def info(self,
            key=None, details=None, recurse=0, relpath=None,
            username=None, password=None):
        return self.call('info',
            key, details, recurse, relpath, username, password)

    async def async_mailbox(self, loop, key,
            skip=0, limit=None, reverse=False,
            username=None, password=None, terms=None):
        return await self.async_call(loop, 'mailbox',
            key, terms, skip, limit, reverse, username, password,
            hide_qs=True)  # Keep passwords out of web logs

    def mailbox(self, key,
            skip=0, limit=None, reverse=False,
            username=None, password=None, terms=None):
        return self.call('mailbox',
            key, terms, skip, limit, reverse, username, password,
            hide_qs=True)  # Keep passwords out of web logs

    async def async_email(self, loop, metadata,
            text=False, data=False, full_raw=False, parts=None,
            username=None, password=None):
        return await self.async_call(loop, 'email',
            metadata[:Metadata.OFS_HEADERS], text, data, full_raw, parts,
            username, password)

    def email(self, metadata,
            text=False, data=False, full_raw=False, parts=None,
            username=None, password=None):
        return self.call('email',
            metadata[:Metadata.OFS_HEADERS], text, data, full_raw, parts,
            username, password)

    def get(self, key, *args, dumbcode=None):
        if dumbcode is not None:
            return self.call('get', key, *args, qs={'dumbcode': dumbcode})
        return self.call('get', key, *args)

    def json(self, *keys):
        return self.call('json', *keys)

    def set(self, key, value, **kwargs):
        return self.call('set', key, value, **kwargs)

    def append(self, key, value):
        return self.call('append', key, value)

    def delete(self, key):
        return self.call('delete', key)


class StorageWorker(BaseWorker, StorageWorkerApi):
    KIND = 'storage'

    SHUTDOWN_IDLE = 300

    PEEK_BYTES = 8192
    BLOCK = 8192

    PARSE_CACHE_MIN = 200
    PARSE_CACHE_TTL = 180

    def __init__(self, status_dir, backend,
            name=KIND, notify=None, log_level=logging.ERROR,
            shutdown_idle=None):
        BaseWorker.__init__(self, status_dir,
            name=name, notify=notify, log_level=log_level,
            shutdown_idle=shutdown_idle)
        self.backend = backend
        self.functions.update({
            b'info':         (True,  self.api_info),
            b'mailbox':      (True,  self.api_mailbox),
            b'email':        (True,  self.api_email),
            b'get':          (False, self.api_get),
            b'json':         (False, self.api_json),
            b'set':          (False, self.api_set),
            b'append':       (False, self.api_append),
            b'delete':       (False, self.api_delete)})

        self.parsed_mailboxes = {}
        self.background_thread = None

    def _expire_parse_cache(self):
        et = time.time() - self.PARSE_CACHE_TTL
        expired = [k for k, v in self.parsed_mailboxes.items() if v[0] <= et]
        for key in expired:
            del self.parsed_mailboxes[key]

    def _background(self, task):
        if self.background_thread is not None:
            self.background_thread.join()
        self.background_thread = threading.Thread(target=task)
        self.background_thread.daemon = True
        self.background_thread.start()

    def pue_to_needinfo(self, pue):
        logging.debug('Need unlock, raising NeedInfoException')
        needs, neo = [], NeedInfoException
        if pue.username:
            needs.append(neo.Need('Username', 'username'))
        if pue.password:
            needs.append(
                neo.Need('Password', 'password', datatype='password'))
        return neo(str(pue), need=needs, resource=pue.resource)

    def api_info(self,
            key, details, recurse, relpath, username, password,
            method=None):
        try:
            self.reply_json(
                self.backend.info(key,
                    details=details, recurse=recurse, relpath=relpath,
                    username=username, password=password))
        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)

    def _prep_filter(self, terms):
        if not terms:
            return (lambda t: t, None)

        terms = (terms or '').split()
        ids = [int(i[3:]) for i in terms if i[:3] == 'id:']
        terms = set([t.lower() for t in terms if t[:3] != 'id:'])

        if not terms:
            return (lambda t: t, ids)

        from moggie.search.extractor import KeywordExtractor
        kwe = KeywordExtractor()

        def msg_terms(r):
            rt = set()
            # Check for substring matches within selected headers
            for term in terms:
                if term in r.headers.lower():
                    rt.add(term)

            # Generate the same keywords as the search index uses
            rt |= kwe.header_keywords(r, r.parsed())[1]
            return rt

        def _filter(result):
            filtered = []
            for r in result:
                if not (terms - msg_terms(r)):
                    # All terms matched!
                    filtered.append(r)
            return filtered

        return (_filter, ids)

    def api_mailbox(self,
            key, terms, skip, limit, reverse, username, password,
            method=None):
        cache_key = '%s/%s/%s/%s' % (key, reverse, username, password)

        _filter, wanted_ids = self._prep_filter(terms)

        self._expire_parse_cache()
        if cache_key in self.parsed_mailboxes and not wanted_ids:
            while (not self.parsed_mailboxes[cache_key][1]
                    and self.background_thread is not None):
                time.sleep(0.1)
            logging.debug('%s: Returning from self.parsed_mailboxes' % key)
            pm = _filter(self.parsed_mailboxes[cache_key][-1])
            beg = skip
            end = skip + (limit or (len(pm)-skip))
            return self.reply_json(pm[beg:end])

        try:
            parser = self.backend.iter_mailbox(key,
                skip=skip, ids=(wanted_ids or None), reverse=reverse,
                username=username, password=password)

            # Ideally, we wouldn't cache anything. But some ops are slow.
            collect = []
            parse_cache = [time.time(), False, collect]

            if limit is None:
                collect.extend(msg for msg in parser)
                logging.debug(
                    '%s: Returning %d messages (u)' % (key, len(collect)))
                if not wanted_ids:
                    parse_cache[1] = True
                    if len(collect) > self.PARSE_CACHE_MIN:
                        self.parsed_mailboxes[cache_key] = parse_cache
                return self.reply_json(_filter(collect))

            result = []
            for msg in parser:
                collect.append(msg)
                if limit and len(result) >= limit:
                    break
                result.extend(_filter([msg]))

            logging.debug('%s: Returning %d messages' % (key, len(result)))
            if not wanted_ids:
                self.parsed_mailboxes[cache_key] = parse_cache
            self.reply_json(result)

        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)

        # Finish in background thread
        if limit and (len(result) >= limit) and not wanted_ids:
            def finish():
                logging.debug('%s: Background completing scan' % key)
                collect.extend(msg for msg in parser)
                parse_cache[1] = True
                self.background_thread = None
            self._background(finish)
        else:
            parse_cache[1] = True

    def api_email(self,
            metadata, text, data, full_raw, parts, username, password,
            method=None):
        metadata = Metadata(*(metadata[:Metadata.OFS_HEADERS] + [b'']))
        try:
            parsed = self.backend.parse_message(metadata,
                username=username, password=password)
        except KeyError as e:
            raise APIException('%s' % e)
        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)

        if text:
            parsed.with_text()
        if data or parts:
            parsed.with_data(only=(parts or None))
        if full_raw:
            parsed.with_full_raw()
        self.reply_json(parsed)

    def api_get(self, key, *args, dumbcode=False, method=None):
        if len(args) > 0 and dumbcode:
            return self.reply(self.HTTP_400)
        try:
            key = str(key, 'latin-1')
            if dumbcode:
                value = dumb_encode_bin(self.backend[key])
            else:
                value = self.backend[key]
            begin = dumb_decode(args[0]) if (len(args) > 0) else  0
            length = len(value)
            length = min(length, dumb_decode(args[1]) if (len(args) > 1) else length)
            length -= begin
        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)
        except KeyError:
            return self.reply(self.HTTP_404)
        except (IndexError, ValueError, TypeError):
            return self.reply(self.HTTP_400)

        c = self.start_sending_data('application/octet-stream', length)
        def sendit():
            p = begin
            for chunk in range(0, 1 + length//self.BLOCK):
                c.send(value[p:min(p+self.BLOCK, begin+length)])
                p += self.BLOCK
            self._client.close()

        if length > self.BLOCK * 5:
            self._background(sendit)
        else:
            sendit()

    def api_json(self, *args, **kwargs):
        """
        This function assumes our backend data is already JSON formatted
        and we are just serving up a dictionary of results.
        """
        try:
            data = [b'{']
            for key in args:
                key = str(key, 'latin-1')
                if key in self.backend:
                    val = self.backend.get(key)
                    try:
                        nd = [('"%s":' % key).encode('latin-1')]
                        if isinstance(val, bytes):
                            nd.append(val)
                        elif isinstance(val, str):
                            nd.append(('"%s"' % val).encode('utf-8'))
                        else:
                            nd.append(to_json(val).encode('utf-8'))
                        nd.append(b',')
                        data.extend(nd)
                    except:
                        logging.exception('api_json failed to encode data')
            if len(data) > 1:
                data[-1] = b'}\n'
                data = b''.join(data)
            else:
                data = b'{}\n'
        except IndexError:
            return self.reply(self.HTTP_400)
        except KeyError:
            return self.reply(self.HTTP_404)

        length = len(data)
        c = self.start_sending_data('application/json', length)
        p = 0
        for chunk in range(0, 1 + length//self.BLOCK):
            c.send(data[p:p+self.BLOCK])
            p += self.BLOCK
        self._client.close()

    def api_set(self, key, value, **kwargs):
        try:
            key = str(key, 'latin-1')
            #print('Setting %s = %s' % (key, dumb_decode(value)))
            self.backend.__setitem__(key, dumb_decode(value), **kwargs)
            self.reply_json({'set': key})
        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)

    def api_append(self, key, data, method=None):
        try:
            key = str(key, 'latin-1')
            if hasattr(self.backend, 'append'):
                self.backend.append(key, dumb_decode(data))
            else:
                self.backend[key] += dumb_decode(data)
            self.reply_json({'appended': key})
        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)

    def api_delete(self, key, method=None):
        try:
            key = str(key, 'latin-1')
            try:
                del self.backend[key]
            except KeyError:
                pass
            self.reply_json({'deleted': key})
        except PleaseUnlockError as pue:
            raise self.pue_to_needinfo(pue)


class StorageWorkers(WorkerPool, StorageWorkerApi):
    def __init__(self, worker_dir, storage=None, **kwargs):
        if storage is None:
            storage = FileStorage(
                relative_to=os.path.expanduser('~'),
                ask_secret=kwargs.get('ask_secret'),
                set_secret=kwargs.get('set_secret'),
                metadata=kwargs.get('metadata'))
        self.fs = storage
        fs_args = (worker_dir, self.fs)
        fs_kwa = {
            'name': 'fs',
            'notify': kwargs.get('notify'),
            'log_level': kwargs.get('log_level', logging.ERROR)}
        self.fs_worker_spec = ('read', StorageWorker, fs_args, fs_kwa)

        self.imap = ImapStorage(
            ask_secret=kwargs.get('ask_secret'),
            set_secret=kwargs.get('set_secret'),
            metadata=kwargs.get('metadata'))
        imap_args = (worker_dir, self.imap)
        imap_kwa = {
            'name': 'imap',
            'notify': kwargs.get('notify'),
            'log_level': kwargs.get('log_level', logging.ERROR)}
        self.imap_worker_spec = ('imap', StorageWorker, imap_args, imap_kwa)

        super().__init__([
            ('read,write', StorageWorker, fs_args, fs_kwa)])

    def auto_add_worker(self, pop, which, capabilities):
        if 'write' in capabilities:
            return None  # There can be only one!
        with self.lock:
            if 'imap' in capabilities:
                self.add_worker(*self.imap_worker_spec)
            else:
                self.add_worker(*self.fs_worker_spec)
            # Set all our read-only and IMAP workers to be daemons.
            # The FS writer is excluded from this policy.
            self.workers[-1][2].daemon = True
            if pop:
                return self.workers.pop(-1)
            else:
                return self.workers[-1][2]

    def choose_worker(self, pop, wait, fn, args, kwargs):
        caps = 'read'
        if fn in ('set', 'append', 'delete'):
            caps = 'write'
        if args and isinstance(args[0], str) and args[0].startswith('imap:'):
            caps = 'imap'
        if fn == 'email' and isinstance(args[0], list):
            md = Metadata(*(args[0][:Metadata.OFS_HEADERS] + [b'']))
            if md.pointers[0].ptr_type == Metadata.PTR.IS_IMAP:
                caps = 'imap'

        worker = self.with_worker(capabilities=caps, pop=pop, wait=wait)
        return worker


if __name__ == '__main__':
    from ..storage.memory import CacheStorage as Storage
    logging.basicConfig(level=logging.DEBUG)

    objects = Storage({
        dumb_encode_asc(b'abc'): [1, 2, 3],
        dumb_encode_asc(b'efg'): "hello world",
        dumb_encode_asc(b'hij'): {1: 2},
        dumb_encode_asc(b'123'): b'{"foo": "bar"}',
        dumb_encode_asc(b'456'): b'0123456789abcdef'})
    print('In storage: %s' % objects.dict)

    sw = StorageWorkers(
            '/tmp', objects,
            name='moggie-test-storage',
            log_level=logging.DEBUG
        ).connect()
    if sw:
        try:
            print(sw.info(details=True))
            print(sw.info(b'123'))
            print(sw.info(b'abc'))
            print('%s' % sw.json(b'abc', b'efg', b'123'))

            hdr, fd = sw.get(b'456', 3, 6)
            assert(3 == int(sw.parse_header(hdr)['Content-Length']))
            assert(b'345' == fd.read())

            try:
                sw.get(99)
                assert(not 'reached')
            except PermissionError:  #FIXME
                pass

            print('** Tests passed, waiting... **')
            sw.join()
        finally:
            sw.terminate()

