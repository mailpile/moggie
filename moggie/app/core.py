import asyncio
import json
import logging
import os
import threading
import traceback
import time

from ..config import AppConfig
from ..storage.files import MailboxFileStorage
from ..workers.importer import ImportWorker
from ..workers.metadata import MetadataWorker
from ..workers.storage import StorageWorker
from ..workers.search import SearchWorker
from ..jmap.core import JMAPSessionResource
from ..jmap.requests import *
from ..jmap.responses import *


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

        self.jmap = {
            'session': self.api_jmap_session}

    # Lifecycle

    def start_workers(self):
        self.metadata = MetadataWorker(self.worker.worker_dir,
            self.worker.profile_dir,
            self.config.get_aes_keys(),
            name='metadata').connect()

        self.search = SearchWorker(self.worker.worker_dir,
            self.worker.profile_dir,
            self.metadata.info()['maxint'],
            self.config.get_aes_keys(),
            notify=self.worker.callback_url('rpc/notify'),
            name='search').connect()

        self.storage = StorageWorker(self.worker.worker_dir,
            MailboxFileStorage(
                relative_to=os.path.expanduser('~'),
                metadata=self.metadata),
            name='fs').connect()

        self.importer = ImportWorker(self.worker.worker_dir,
            fs_worker=self.storage,
            app_worker=self.worker,
            search_worker=self.search,
            metadata_worker=self.metadata,
            name='importer').connect()

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

    def startup_tasks(self):
        self.start_workers()

    def shutdown_tasks(self):
        self.stop_workers()


    # Public API

    async def api_jmap_mailbox(self, access, jmap_request):
        # FIXME: Make sure access grants right to read mailboxes directly
        info = await async_run_in_thread(self.storage.mailbox,
            jmap_request['mailbox'],
            limit=jmap_request['limit'],
            skip=jmap_request['skip'])
        watched = False
        return ResponseMailbox(jmap_request, info, watched)

    async def api_jmap_search(self, access, jmap_request):
        # FIXME: Make sure access allows requested contexts
        #        Make sure we set tag_namespace based on the context
        def perform_search():
            return list(self.metadata.metadata(
                self.search.search(
                        jmap_request['terms'],
                        tag_namespace=jmap_request.get('tag_namespace', None),
                        mask_deleted=jmap_request.get('mask_deleted', True)
                    )['hits'],
                sort=self.search.SORT_DATE_DEC,  # FIXME: configurable?
                skip=jmap_request.get('skip', 0),
                limit=jmap_request.get('limit', None)))
        results = await async_run_in_thread(perform_search)
        return ResponseSearch(jmap_request, results)

    async def api_jmap_counts(self, access, jmap_request):
        # FIXME: Make sure access allows requested contexts
        #        Make sure we set tag_namespace based on the context
        # FIXME: Actually search/count! Async, in a thread, gathering the
        #        results may take time.
        return ResponseCounts(jmap_request, {})

    async def api_jmap_email(self, access, jmap_request):
        # FIXME: Does this user have access to this email?
        #        How will that be determined? Probably a token that
        #        comes from viewing a search result or mailbox?
        #        Seems we should decide that before making any efforts
        info = await async_run_in_thread(self.storage.email,
            jmap_request['metadata'],
            text=jmap_request.get('text', False),
            data=jmap_request.get('data', False))
        return ResponseEmail(jmap_request, info)

    async def api_jmap_contexts(self, access, jmap_request):
        # FIXME: Only return contexts this access level grants use of
        all_contexts = self.config.contexts
        contexts = [all_contexts[k].as_dict() for k in sorted(access.roles)]
        return ResponseContexts(jmap_request, contexts)

    async def api_jmap_add_to_index(self, access, jmap_request):
        # FIXME: Access questions, context settings...
        result = await async_run_in_thread(self.importer.import_search,
            jmap_request['search'],
            jmap_request.get('initial_tags', []),
            force=jmap_request.get('force', False))
        return ResponsePing(jmap_request)  # FIXME

    async def api_jmap(self, access, client_request):
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
            result = await self.api_jmap_mailbox(access, jmap_request)
        elif type(jmap_request) == RequestSearch:
            result = await self.api_jmap_search(access, jmap_request)
        elif type(jmap_request) == RequestCounts:
            result = await self.api_jmap_counts(access, jmap_request)
        elif type(jmap_request) == RequestEmail:
            result = await self.api_jmap_email(access, jmap_request)
        elif type(jmap_request) == RequestContexts:
            result = await self.api_jmap_contexts(access, jmap_request)
        elif type(jmap_request) == RequestAddToIndex:
            result = await self.api_jmap_add_to_index(access, jmap_request)
        elif type(jmap_request) == RequestPing:
            result = ResponsePing(jmap_request)

        if result is not None:
            code, json_result = 200, json.dumps(result, indent=2)
            if type(jmap_request) != RequestPing:
                logging.debug('<< %s' % json_result[:256])
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
        await self.worker.broadcast(ResponseNotification(notification))
        self.worker.reply_json({'ok': 'thanks'})

    async def rpc_check_result(self, request_id, **kwargs):
        pass

    async def rpc_jmap(self, request, **kwargs):
        try:
            rv = await self.api_jmap(self.config.access_zero(), request)
            self.worker.reply_json(rv['_result'])
        except:
            logging.exception('rpc_jmap failed %s' % (request,))
            self.worker.reply_json({'error': 'FIXME'})

    def rpc_session_resource(self, **kwargs):
        jsr = AppSessionResource(self, self.config.access_zero())
        self.worker.reply_json(jsr)

    def rpc_crypto_status(self, **kwargs):
        locked = (self.config.has_crypto_enabled and not self.config.aes_key)
        self.worker.reply_json({
            'encrypted': self.config.has_crypto_enabled,
            'locked': locked})

    def rpc_get_access_token(self, **kwargs):
        a0 = self.config.access_zero()
        token, expiration = a0.get_fresh_token()
        self.worker.reply_json({
            'token': token,
            'expires': expiration})

