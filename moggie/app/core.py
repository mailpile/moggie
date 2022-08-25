import asyncio
import json
import logging
import os
import threading
import traceback
import time
import sys

from ..config import APPNAME_UC, APPVER, AppConfig
from ..jmap.core import JMAPSessionResource
from ..jmap.requests import *
from ..jmap.responses import *
from ..storage.files import FileStorage
from ..util.dumbcode import dumb_decode
from ..workers.importer import ImportWorker
from ..workers.metadata import MetadataWorker
from ..workers.storage import StorageWorker
from ..workers.search import SearchWorker


async def async_run_in_thread(method, *m_args, **m_kwargs):
    def runner(l, q):
        time.sleep(0.1)
        try:
            rv = method(*m_args, **m_kwargs)
        except:
            logging.exception('async in thread crashed, %s' % (method,))
            rv = None
        l.call_soon_threadsafe(q.put_nowait, rv)

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    thr = threading.Thread(target=runner, args=(loop, queue))
    thr.daemon = True
    thr.start()
    return await queue.get()


def run_async_in_thread(method, *m_args, **m_kwargs):
    result = []
    def runner():
        result.append(asyncio.run(method(*m_args, **m_kwargs)))
    thr = threading.Thread(target=runner)
    thr.daemon = True
    thr.start()
    thr.join()
    return result[0]


class AppSessionResource(JMAPSessionResource):
    def __init__(self, app, access):
        super().__init__(self)
        self.app = app
        self.access = access
        if access.username:
            self.username = access.username
        accounts = {}
        for context, role in access.roles.items():
            accounts[context] = {}  #FIXME: Present context as JMAP
        self.accounts = accounts


class AppCore:
    PYCLI_BANNER = ("""
%s %s / Python %s

Welcome the interactive Moggie debug console! This is a thread of the
main app worker. Hints:

        """).strip() % (APPNAME_UC, APPVER, sys.version.splitlines()[0])

    def __init__(self, app_worker):
        self.work_dir = os.path.normpath(# FIXME: This seems a bit off
            os.path.join(app_worker.worker_dir, '..'))

        self.worker = app_worker
        self.config = AppConfig(self.work_dir)

        self.rpc_functions = {
            b'rpc/notify':            (True, self.rpc_notify),
            b'rpc/jmap':              (True, self.rpc_jmap),
            b'rpc/jmap_session':      (True, self.rpc_session_resource),
            b'rpc/crypto_status':     (True, self.rpc_crypto_status),
            b'rpc/get_access_token':  (True, self.rpc_get_access_token)}

        self._schedule = []
        self.metadata = None
        self.importer = None
        self.storage = None
        self.search = None
        self.ticker = None
        self.jmap = {
            'session': self.api_jmap_session}

    # Lifecycle

    async def tick(self):
        now = int(time.time())
        # FIXME: Is there something we should be doing here?
        self.schedule(now + 120, self.tick())

    def schedule(self, when, job):
        self._schedule.append((when, job))
        self._schedule.sort(key=lambda i: (i[0], repr(i[1])))

    async def ticker_task(self):
        while True:
            now = time.time()
            while self._schedule and self._schedule[0][0] <= now:
                t, job = self._schedule.pop(0)
                asyncio.create_task(job)
            await asyncio.sleep(1)

    def start_encrypting_workers(self):
        try:
            notify_url = self.worker.callback_url('rpc/notify')
            aes_keys = self.config.get_aes_keys()
        except:
            return False

        log_level = int(self.config.get(
            self.config.GENERAL, 'log_level', fallback=logging.ERROR))

        missing_metadata = self.metadata is None
        if missing_metadata:
            self.metadata = MetadataWorker(self.worker.worker_dir,
                self.worker.profile_dir,
                aes_keys,
                notify=notify_url,
                name='metadata',
                log_level=log_level).connect()

        if self.search is None:
            self.search = SearchWorker(self.worker.worker_dir,
                self.worker.profile_dir,
                self.metadata.info()['maxint'],
                aes_keys,
                notify=notify_url,
                name='search',
                log_level=log_level).connect()

        if missing_metadata and self.metadata and self.storage:
            # Restart workers that want to know about our metadata store
            self.storage.quit()
            self.start_workers(start_encrypted=False)

        if self.importer is None:
            self.importer = ImportWorker(self.worker.worker_dir,
                fs_worker=self.storage,
                app_worker=self.worker,
                search_worker=self.search,
                metadata_worker=self.metadata,
                notify=notify_url,
                name='importer',
                log_level=log_level).connect()

        return True

    def start_workers(self, start_encrypted=True):
        notify_url = self.worker.callback_url('rpc/notify')
        log_level = int(self.config.get(
            self.config.GENERAL, 'log_level', fallback=logging.ERROR))

        if self.ticker is None:
            self._ticker = self.ticker_task()
            loop = asyncio.get_event_loop()
            loop.create_task(self._ticker)
            loop.create_task(self.tick())

        if start_encrypted:
            if not self.config.has_crypto_enabled:
                from moggie.crypto.passphrases import generate_passcode
                logging.info('Generating initial (unlocked) encryption keys.')
                passphrase = generate_passcode(groups=5)
                self.config[self.config.SECRETS]['passphrase'] = passphrase
                self.config.provide_passphrase(passphrase)
                self.config.generate_master_key()

            if not self.start_encrypting_workers():
                logging.warning(
                    'Failed to start encrypting workers. Need login?')

        self.storage = StorageWorker(self.worker.worker_dir,
            FileStorage(
                relative_to=os.path.expanduser('~'),
                metadata=self.metadata),
            notify=notify_url,
            name='fs',
            log_level=log_level).connect()

        return True

    def stop_workers(self):
        # The order here may matter, we ask the "higher level" workers
        # to shut down first, before shutting down low level systems.
        all_workers = [
            self.importer,
            self.storage,   # This one talks to the metadata index!
            self.search,
            self.metadata]
        for p in (1, 2, 3):
            for worker in all_workers:
                try:
                    if p == 1:
                        worker.quit()
                    if p == 2 and worker.is_alive():
                        worker.join(1)
                    if p == 3 and worker.is_alive():
                        worker.terminate()
                except:
                    pass
            time.sleep(0.1)

        self.importer = self.storage = self.search = self.metadata = None

    def startup_tasks(self):
        self.start_workers()
        self._stashed_results = {}

    def start_pycli(self):
        def RunCLI():
            import readline
            import code
            import io

            # Reopen stdin, since multiprocessing.Process closes it
            sys.stdin = io.TextIOWrapper(io.BufferedReader(os.fdopen(0)))

            env = globals()
            env['moggie'] = self
            code.InteractiveConsole(locals=env).interact(self.PYCLI_BANNER)

            print('Shutting down %s, good-bye!' % APPNAME_UC)
            self.worker.quit()

        pycli = threading.Thread(target=RunCLI)
        pycli.daemon = True
        pycli.start()

    def shutdown_tasks(self):
        self.stop_workers()

    def keep_result(self, rid, rv):
        self._results[rid] = (time.time(), rv)

    def _is_locked(self):
        return (self.config.has_crypto_enabled and not self.config.aes_key)


    # Public API

    async def api_webroot(self, req_env):
        return 'FIXME: Hello world'

    async def api_jmap_mailbox(self, conn_id, access, jmap_request):
        # FIXME: Make sure access grants right to read mailboxes directly
        def load_mailbox():
            return self.storage.mailbox(
                jmap_request['mailbox'],
                limit=jmap_request['limit'],
                skip=jmap_request['skip'])
        info = await async_run_in_thread(load_mailbox)
        watched = False
        return ResponseMailbox(jmap_request, info, watched)

    async def api_jmap_search(self, conn_id, access, jmap_request):
        # FIXME: Make sure access allows requested contexts
        ctx = jmap_request['context']
        if ctx not in self.config.contexts:
            raise ValueError('Invalid context: %s' % ctx)
        tag_namespace = self.config.get(ctx, 'tag_namespace', fallback=None)

        def perform_search():
            return list(self.metadata.with_caller(conn_id).metadata(
                self.search.with_caller(conn_id).search(
                        jmap_request['terms'],
                        tag_namespace=tag_namespace,
                        mask_deleted=jmap_request.get('mask_deleted', True)
                    )['hits'],
                sort=self.search.SORT_DATE_DEC,  # FIXME: configurable?
                only_ids=jmap_request.get('only_ids', False),
                threads=jmap_request.get('threads', False),
                skip=jmap_request['skip'],
                limit=jmap_request['limit'],
                raw=True))

        jmap_request['skip'] = jmap_request.get('skip') or 0
        jmap_request['limit'] = jmap_request.get('limit', None)
        if self.metadata and self.search:
            results = await async_run_in_thread(perform_search)
            return ResponseSearch(jmap_request, results)
        else:
            return ResponsePleaseUnlock(jmap_request)

    async def api_jmap_counts(self, conn_id, access, jmap_request):
        # FIXME: Make sure access allows requested contexts
        ctx = jmap_request['context']
        if ctx not in self.config.contexts:
            raise ValueError('Invalid context: %s' % ctx)
        tag_namespace = self.config.get(ctx, 'tag_namespace', fallback=None)

        def perform_counts():
            counts = {}
            for terms in jmap_request['terms_list']:
                result = self.search.with_caller(conn_id).search(terms,
                    tag_namespace=tag_namespace,
                    mask_deleted=jmap_request.get('mask_deleted', True))
                counts[terms] = dumb_decode(result['hits']).count()
            return counts

        if self.search:
            counts = await async_run_in_thread(perform_counts)
            return ResponseCounts(jmap_request, counts)
        else:
            return ResponsePleaseUnlock(jmap_request)

    async def api_jmap_email(self, conn_id, access, jmap_request):
        # FIXME: Does this user have access to this email?
        #        How will that be determined? Probably a token that
        #        comes from viewing a search result or mailbox?
        #        Seems we should decide that before making any efforts
        def get_email():
            return self.storage.with_caller(conn_id).email(
                jmap_request['metadata'],
                text=jmap_request.get('text', False),
                data=jmap_request.get('data', False))
        info = await async_run_in_thread(get_email)
        return ResponseEmail(jmap_request, info)

    async def api_jmap_contexts(self, conn_id, access, jmap_request):
        # FIXME: Only return contexts this access level grants use of
        all_contexts = self.config.contexts
        contexts = [all_contexts[k].as_dict() for k in sorted(access.roles)]
        return ResponseContexts(jmap_request, contexts)

    async def api_jmap_unlock(self, conn_id, access, jmap_request):
        if self._is_locked() and jmap_request.get('passphrase'):
            try:
                self.config.provide_passphrase(jmap_request['passphrase'])
                self.start_encrypting_workers()
            except PermissionError:
                return ResponsePleaseUnlock(jmap_request)
        return ResponseUnlocked(jmap_request)

    async def api_jmap_changepass(self, conn_id, access, jmap_request):
        newp = jmap_request.get('new_passphrase')
        oldp = jmap_request.get('old_passphrase')
        if not oldp:
            oldp = self.config.get(
                self.config.SECRETS, 'passphrase', fallback=None)

        if (not self._is_locked()) and newp and oldp:
            try:
                self.config.provide_passphrase(oldp)
                self.stop_workers()
                # FIXME: Do something sensible about password recovery!
                #        ... before or after we actually change keys?
                self.config.change_config_key(newp)
                self.config.change_master_key()
                # FIXME: Check if user requested closing sessions
                self.config.save()
                return ResponseNotification({
                    # FIXME: Add trigger for a recovery dialog?
                    'message': 'Passphrase changed and keys rotated!'})
            except PermissionError:
                return ResponseNotification({
                    'message': 'Passphrase incorrect or permission denied.'})
            except:
                logging.exception('Change Passphrase failed')
            finally:
                self.start_workers()
        return None

    async def api_jmap_add_to_index(self, conn_id, access, jmap_request):
        # FIXME: Access questions, context settings...
        def import_search():
            return self.importer.with_caller(conn_id).import_search(
                jmap_request['search'],
                jmap_request.get('initial_tags', []),
                force=jmap_request.get('force', False))
        if self.metadata and self.search:
            result = await async_run_in_thread(import_search)
            return ResponsePing(jmap_request)  # FIXME
        else:
            return ResponsePleaseUnlock(jmap_request)

    async def api_jmap(self, conn_id, access, client_request, internal=False):
        # The JMAP API sends multiple requests in a blob, and wants some magic
        # interpolation as well. Where do we implement that? Is there a lib we
        # should depend upon? DIY?
        #
        # results = {}
        # for jr in jmap_request_iter(jmap_request, results):
        #     results[jr.id] = await self.jmap[jr.method](access, jr)
        # return {... results ...}
        #
        try:
            jmap_request = to_jmap_request(client_request)
        except KeyError as e:
            logging.warning('Invalid request: %s' % e)
            return {'code': 500}

        # FIXME: This is a hack
        result = None
        if type(jmap_request) == RequestMailbox:
            result = await self.api_jmap_mailbox(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestSearch:
            result = await self.api_jmap_search(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestCounts:
            result = await self.api_jmap_counts(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestEmail:
            result = await self.api_jmap_email(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestContexts:
            result = await self.api_jmap_contexts(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestAddToIndex:
            result = await self.api_jmap_add_to_index(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestUnlock:
            result = await self.api_jmap_unlock(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestChangePassphrase:
            result = await self.api_jmap_changepass(conn_id, access, jmap_request)
        elif type(jmap_request) == RequestPing:
            result = ResponsePing(jmap_request)

        if internal:
            return result

        if result is not None:
            code, json_result = 200, json.dumps(result)
        else:
            code = 400
            result = {'error': 'Unknown %s' % type(jmap_request)}
            json_result = json.dumps(result)

        return {
            'code': code,
            '_result': result,
            'mimetype': 'application/json',
            'body': bytes(json_result, 'utf-8')}

    def api_jmap_session(self, access):
        # FIXME: What does this user have access to?
        jsr = AppSessionResource(self, access)
        return {
            'code': 200,
            'mimetype': 'application/json',
            'body': str(jsr)}


    # Internal API

    async def rpc_notify(self, notification, **kwargs):
        only = None
        if kwargs.get('caller'):
            only = kwargs.get('caller')
            if not isinstance(only, list):
                only = [only]
        await self.worker.broadcast(
            ResponseNotification(notification),
            only=only)
        self.worker.reply_json({'ok': 'thanks'})

    async def rpc_check_result(self, request_id, **kwargs):
        pass

    async def rpc_jmap(self, request, **kwargs):
        try:
            rv = await self.api_jmap(None, self.config.access_zero(), request)
            self.worker.reply_json(rv['_result'])
        except:
            logging.exception('rpc_jmap failed %s' % (request,))
            self.worker.reply_json({'error': 'FIXME'})

    def rpc_session_resource(self, **kwargs):
        jsr = AppSessionResource(self, self.config.access_zero())
        self.worker.reply_json(jsr)

    def rpc_crypto_status(self, **kwargs):
        all_started = self.metadata and self.search and self.importer
        unlocked = self.config.get(
            self.config.SECRETS, 'passphrase', fallback=False) and True
        self.worker.reply_json({
            'encrypted': self.config.has_crypto_enabled,
            'unlocked': unlocked,
            'locked': self._is_locked()})

    def rpc_get_access_token(self, **kwargs):
        a0 = self.config.access_zero()
        token, expiration = a0.get_fresh_token()
        self.worker.reply_json({
            'token': token,
            'expires': expiration})

