import asyncio
import json
import os
import threading
import time

from ..config import AppConfig
from ..storage.files import FileStorage
from ..storage.metadata import MetadataStore
from ..workers.importer import ImportWorker
from ..workers.storage import StorageWorker
from ..workers.search import SearchWorker
from ..jmap.core import JMAPSessionResource
from ..jmap.requests import *
from ..jmap.responses import *


async def async_run_in_thread(method, *m_args, **m_kwargs):
    def runner(l, q):
        l.call_soon_threadsafe(q.put_nowait, method(*m_args, **m_kwargs))

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    thr = threading.Thread(target=runner, args=(loop, queue))
    thr.daemon = True
    thr.start()

    return await queue.get()


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
        self.metadata = None

        self.rpc_functions = {
            b'rpc/jmap_session':      (True, self.rpc_session_resource),
            b'rpc/crypto_status':     (True, self.rpc_crypto_status),
            b'rpc/get_access_token':  (True, self.rpc_get_access_token),
            b'rpc/register_metadata': (True, self.rpc_register_metadata)}

        self.jmap = {
            'session': self.api_jmap_session}

    # Lifecycle

    def start_workers(self):
        self.email_accounts = {}
        self.storage = StorageWorker(self.worker.worker_dir,
            FileStorage(relative_to=os.path.expanduser('~')),
            name='fs').connect()
        self.search = SearchWorker(self.worker.worker_dir,
            '/tmp', b'FIXME', len(self.metadata),
            name='search').connect()
        self.importer = ImportWorker(self.worker.worker_dir,
            name='importer').connect()

    def stop_workers(self):
        # The order here may matter
        all_workers = (self.importer, self.search, self.storage)
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

    def load_metadata(self):
        self.metadata = MetadataStore(
            os.path.join(self.worker.profile_dir, 'metadata'), 'metadata',
            aes_key=b'bogus AES key')  # FIXME

    def startup_tasks(self):
        self.load_metadata()
        self.start_workers()

    def shutdown_tasks(self):
        self.stop_workers()


    # Public API

    async def api_jmap_mailbox(self, access, jmap_request):
        info = await async_run_in_thread(self.storage.mailbox,
            jmap_request['mailbox'],
            limit=jmap_request['limit'],
            skip=jmap_request['skip'])
        watched = False
        return ResponseMailbox(jmap_request, info, watched)

    async def api_jmap_search(self, access, jmap_request):
        # FIXME: Actually search! Async, in a thread, gathering the
        #        metadata may take time.
        return ResponseSearch(jmap_request, [])

    async def api_jmap_counts(self, access, jmap_request):
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
        all_contexts = self.config.contexts
        contexts = [all_contexts[k].as_dict() for k in sorted(access.roles)]
        return ResponseContexts(jmap_request, contexts)

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
            print('Invalid request: %s' % e)
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
        elif type(jmap_request) == RequestPing:
            result = ResponsePing(jmap_request)

        if result is not None:
            code, result = 200, json.dumps(result, indent=2)
            if type(jmap_request) != RequestPing:
                print('<< %s' % result[:1024])
        else:
            code = 400
            result = json.dumps({'error': 'Unknown %s' % type(jmap_request)})

        return {
            'code': code,
            'mimetype': 'application/json',
            'body': bytes(result, 'utf-8')}

    def api_jmap_session(self, access):
        # FIXME: What does this user have access to?
        jsr = AppSessionResource(self, access)
        return {
            'code': 200,
            'mimetype': 'application/json',
            'body': str(jsr)}


    # Internal API

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

    def rpc_register_metadata(self, emails, *args, **kwargs):
        added = []
        for m in (Metadata(*e) for e in emails):
             idx = self.metadata.add_if_new(m)
             if idx is not None:
                 added.append(idx)

        # FIXME: Tag these new messages as INCOMING.
        #        Other tags as well? Which context is this?

        self.worker.reply_json(added)

