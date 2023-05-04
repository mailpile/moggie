import asyncio
import base64
import logging
import os
import random
import re
import threading
import traceback
import time
import sys

from ..config import APPNAME_UC, APPVER, AppConfig, AccessConfig
from ..config.helpers import DictItemProxy, EncodingListItemProxy
from ..api.core import APISessionResource
from ..api.requests import *
from ..api.responses import *
from ..storage.files import FileStorage
from ..util.dumbcode import dumb_decode, to_json, from_json
from ..workers.importer import ImportWorker
from ..workers.metadata import MetadataWorker
from ..workers.storage import StorageWorker
from ..workers.search import SearchWorker
from .cli import CLI_COMMANDS


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


class AppSessionResource(APISessionResource):
    def __init__(self, app, access):
        super().__init__(self)
        self.app = app
        self.access = access
        if access.username:
            self.username = access.username
        accounts = {}
        contexts = app.config.contexts
        for ctx, role in access.roles.items():
            context = contexts[ctx]
            remote = context.remote_context_url or False
            accounts[ctx.split(' ', 1)[1]] = APIAccount({
                'name': context.name,
                'isPersonal': ('A' in role or 'a' in role) and not remote,
                'isReadOnly': not (remote or bool(role.strip('rpe'))),
                'accountCapabilities': APIAccountCapabilities(remote=remote)})
        self.accounts = accounts


class AppCore:
    PYCLI_BANNER = ("""
%s %s / Python %s

Welcome the interactive Moggie debug console! This is a thread of the
main app worker. Hints:

        """).strip() % (APPNAME_UC, APPVER, sys.version.splitlines()[0])

    EXT_TO_MIME = {
       'html': 'text/html; charset="utf-8"',
       'txt': 'text/plain; charset="utf-8"',
       'js': 'text/javascript; charset="utf-8"',
       'css': 'text/css',
       'png': 'image/png',
       'jpg': 'image/jpeg',
       'svg': 'image/svg',
       'eot': 'application/vnd.ms-fontobject',
       'ttf': 'font/ttf',
       'woff': 'font/woff'}

    def __init__(self, app_worker):
        self.work_dir = os.path.normpath(# FIXME: This seems a bit off
            os.path.join(app_worker.worker_dir, '..'))

        self.worker = app_worker
        self.config = AppConfig(self.work_dir)

        self.rpc_functions = {
            b'rpc/notify':            (True, self.rpc_notify),
            b'rpc/api':               (True, self.rpc_api),
            b'rpc/ask_secret':        (True, self.rpc_ask_secret),
            b'rpc/set_secret':        (True, self.rpc_set_secret),
            b'rpc/jmap_session':      (True, self.rpc_session_resource),
            b'rpc/crypto_status':     (True, self.rpc_crypto_status),
            b'rpc/get_access_token':  (True, self.rpc_get_access_token)}

        self._schedule = []
        self.counter = int(time.time()) - 1663879773
        self.metadata = None
        self.importer = None
        self.storage = None
        self.openpgp_workers = {}
        self.stores = {}
        self.search = None
        self.ticker = None

        # FIXME: Make this customizable somehow
        self.theme = {'_unused_body_bg': '#fff'}
        self.asset_paths = [
            os.path.join(self.work_dir, 'assets'),
            os.path.normpath(os.path.join(
                os.path.dirname(__file__), '..', '..', 'assets'))]

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

    def _get_openpgp_worker(self, ctx):
        from moggie.workers.openpgp import OpenPGPWorker

        context = self.config.contexts[ctx]
        worker_name = OpenPGPWorker.KIND
        if context.tag_namespace:
            worker_name += '-' + context.tag_namespace

        if worker_name not in self.openpgp_workers:
            ksc, sopc, = context.get_openpgp_settings()
            log_level = self.worker.log_level
            worker = OpenPGPWorker(
                self.worker.worker_dir, self.worker.profile_dir,
                self.config.get_aes_keys(),
                name=worker_name,
                keystore_config=ksc,
                sop_config=sopc,
                tag_namespace=context.tag_namespace,
                search=self.search,
                metadata=self.metadata,
                log_level=log_level)
            if worker.connect():
                self.openpgp_workers[worker_name] = worker

        return self.openpgp_workers[worker_name]

    def start_encrypting_workers(self):
        try:
            notify_url = self.worker.callback_url('rpc/notify')
            aes_keys = self.config.get_aes_keys()
        except:
            return False

        log_level = self.worker.log_level

        missing_metadata = self.metadata is None
        if missing_metadata:
            self.metadata = MetadataWorker(
                self.worker.worker_dir, self.worker.profile_dir,
                aes_keys,
                notify=notify_url,
                name='metadata',
                log_level=log_level).connect()

        if self.search is None:
            self.search = SearchWorker(
                self.worker.worker_dir, self.worker.profile_dir,
                self.metadata,
                aes_keys,
                notify=notify_url,
                name='search',
                log_level=log_level).connect()

        if missing_metadata and self.metadata and self.storage:
            # Restart workers that want to know about our metadata store
            self.storage.quit()
            self.start_workers(start_encrypted=False)

        self._get_openpgp_worker(self.config.CONTEXT_ZERO)

        return True

    def start_workers(self, start_encrypted=True):
        notify_url = self.worker.callback_url('rpc/notify')
        log_level = self.worker.log_level

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
                ask_secret=self._fs_ask_secret,
                set_secret=self._fs_set_secret,
                relative_to=os.path.expanduser('~'),
                metadata=self.metadata),
            notify=notify_url,
            name='fs',
            log_level=log_level).connect()

        if (self.storage and self.search and self.metadata
                and (self.importer is None)):
            self.importer = ImportWorker(self.worker.worker_dir,
                fs_worker=self.storage,
                app_worker=self.worker,
                search_worker=self.search,
                metadata_worker=self.metadata,
                notify=notify_url,
                name='importer',
                log_level=log_level).connect()

        return True

    def stop_workers(self):
        # The order here may matter, we ask the "higher level" workers
        # to shut down first, before shutting down low level systems.
        all_workers = []
        all_workers += self.openpgp_workers.values()
        all_workers += [
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
        self.openpgp_workers = {}

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
        self.config.save()

    def keep_result(self, rid, rv):
        self._results[rid] = (time.time(), rv)

    def _fs_ask_secret(self, context, resource):
        rv = self.worker.call('rpc/ask_secret', context, resource)
        if rv is None:
            raise PleaseUnlockError('Secret unavailable', resource=resource)
        return rv

    def _fs_set_secret(self, context, resource, secret, ttl):
        return self.worker.call('rpc/set_secret',
            context, resource, secret, ttl)

    def _is_locked(self):
        return (self.config.has_crypto_enabled and not self.config.aes_key)

    def _config_new_section(self, create):
        while True:
            section = '%s%x' % ({
                    'access': self.config.ACCESS_PREFIX,
                    'account': self.config.ACCOUNT_PREFIX,
                    'context': self.config.CONTEXT_PREFIX
                }[create], self.counter % 0x10000)
            self.counter += 1
            if section not in self.config:
                break
        return section

    # Public API

    async def api_webroot(self, req_env):
        return 'FIXME: Hello world'

    async def api_req_config_set(self, conn_id, access, api_request):
        czero = self.config.CONTEXT_ZERO

        create = api_request.get('new') or ''
        section = api_request.get('section') or ''
        updates = api_request.get('updates') or []

        # Sanity and access checks
        #
        # FIXME: Allow more non-admin config changes?
        try:
            if (create and section) or not (create or section):
                raise ValueError('Need one of create or section (not both)')
            elif section == self.config.SECRETS:
                # This is just too dangerous, at least for now
                raise PermissionError('Access denied')
            elif access.config_key == self.config.ACCESS_ZERO:
                # Access Zero can do anything they like
                pass
            elif section.startswith(self.config.CONTEXT_PREFIX):
                # User has admin rights on the section? OK.
                _, _, _ = access.grants(section, AccessConfig.GRANT_ACCESS)
            elif updates and (
                    section.startswith(self.config.ACCESS_PREFIX)
                    or (create == 'account')):
                # If the user is ONLY granting/revoking access to contexts they
                # are in charge of, then allow it.
                for update in updates:
                    if update['variable'] not in 'roles':
                        raise PermissionError('Access denied')
                    if update['op'] not in ('dict_set', 'dict_del'):
                        raise PermissionError('Invalid operation')
                    _, _, _ = access.grants(
                        update['dict_key'], AccessConfig.GRANT_ACCESS)
            else:
                # Other changes require "root" access
                _, _, _ = access.grants(czero, AccessConfig.GRANT_ACCESS)
        except (ValueError, NameError, TypeError):
            # FIXME: this might not be the most helpful thing to do here
            raise PermissionError('Access denied')

        result = {'updates': []}
        errors = []

        if create:
            section = self._config_new_section(create)

        # FIXME: The access object should give us some more details, e.g.
        #        IP address if the user is remote - this is our audit log!
        logging.info('[config] %s [%s] for %s' % (
            'Creating' if create else 'Updating', section, access.name))

        logging.info('SET: create=%s section=%s updates=%s'
            % (create, section, updates))
        with self.config:
          for update in updates:
            op, var = update['op'], update.get('variable')
            if op == 'remove_section'  and not var:
                self.config.remove_section(section)
            elif op == 'set':
                self.config.set(section, var, update['value'])
            elif op in ('del', 'delete', 'remove'):
                self.config.remove_option(section, var)
            elif op in ('dict_set', 'dict_del'):
                dp = DictItemProxy(self.config, section, var)
                if op == 'dict_set':
                    dp[update['dict_key']] = update['dict_val']
                elif op == 'dict_del':
                    del dp[update['dict_key']]
            elif op in ('list_add_unique', 'list_add', 'list_set', 'list_del'):
                lp = EncodingListItemProxy(self.config, section, var)

                val = update.get('list_val')
                cs = update.get('case_sensitive', True)
                def _exists():
                    if cs:
                        return (val in lp) and val or None
                    for v in lp:
                        if (v.lower() == val.lower()):
                            return v
                    return None

                if op == 'list_add':
                    lp.append(val)
                elif op == 'list_add_unique':
                    if _exists() is None:
                        lp.append(val)
                elif op == 'list_set':
                    lp[int(update['list_key'])] = val
                elif op == 'list_del':
                    ex = _exists()
                    if ex is not None:
                        lp.remove(ex)
                        if len(lp) == 0:
                            self.config.remove_option(section, var)
            elif (section.startswith(self.config.ACCESS_PREFIX)
                   and op == 'new_access_token'):
                ttl = update.get('ttl')
                self.config.all_access[section].new_token(ttl=ttl)
            else:
                errors.append('Unknown op: %s(%s)' % (op, var))
                logging.info('ConfigSet error: %s' % errors[-1])

        return ResponseConfigSet(api_request, result, error=', '.join(errors))

    async def api_req_set_secret(self, conn_id, access, api_request):
        ctx = api_request.get('context') or self.config.CONTEXT_ZERO
        # Will raise ValueError or NameError if access denied
        # FIXME: Is this a reasonable permission requirement?
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_TAG_RW)

        key = api_request['key']
        ttl = int(api_request['ttl'] or 0)
        secret = api_request['secret']
        context = self.config.get_context(ctx)
        if not context:
            raise ValueError('No such context')
        if not key:
            raise ValueError('Set which secret?')

        with self.config:
            context.set_secret(key, secret, ttl=ttl)

        return ResponseConfigSet(api_request, {'set_secret': key, 'ttl': ttl})

    async def api_req_config_get(self, conn_id, access, api_request):
        result = {}
        czero = self.config.CONTEXT_ZERO
        which = api_request.get('which')
        error = None

        def _fill(name, all_items, context, prefix, roles):
            if which and which.startswith(prefix):
                if access.grants(context, roles):
                    result[name] = {which: all_items[which].as_dict()}
                else:
                    error = 'Access denied: %s/%s' % (name, which)
            elif access.grants(context, roles):
                result[name] = dict(
                    (k, v.as_dict()) for k,v in all_items.items())
            else:
                error = 'Access denied: %s' % name

        if api_request.get('access'):
            _fill('access', self.config.all_access, czero,
                self.config.ACCESS_PREFIX, AccessConfig.GRANT_ACCESS)
        if api_request.get('accounts'):
            _fill('accounts', self.config.accounts, czero,
                self.config.ACCOUNT_PREFIX, AccessConfig.GRANT_ACCESS)
        if api_request.get('identities'):
            _fill('identities', self.config.identities, czero,
                self.config.IDENTITY_PREFIX, AccessConfig.GRANT_ACCESS)

        if api_request.get('urls'):
            result['urls'] = [self.worker.url.rsplit('/', 1)[0]]
            kite_name = self.config.get(
                self.config.GENERAL, 'kite_name', fallback=None)
            if kite_name:
                result['urls'].append('https://%s' % kite_name)

        if api_request.get('contexts'):
            contexts = self.config.contexts
            if which and which.startswith(self.config.CONTEXT_PREFIX):
                if access.grants(which, AccessConfig.GRANT_ACCESS):
                    result['contexts'] = {
                        which: contexts[which].as_dict(deep=False)}
                else:
                    error = 'Access denied: contexts/%s' % (which,)
            elif access.grants(czero, AccessConfig.GRANT_ACCESS):
                result['contexts'] = dict(
                    (k, v.as_dict(deep=False)) for k,v in contexts.items())
            else:
                error = 'Access denied: contexts'

        return ResponseConfigGet(api_request, result, error=error)

    async def api_req_mailbox(self, conn_id, access, api_request):
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx,
            AccessConfig.GRANT_READ + AccessConfig.GRANT_FS)

        loop = asyncio.get_event_loop()
        async def load_mailbox():
            # FIXME: Triage local/remote here? Hmm.
            # Note: This might return a "please login" if the mailbox
            #       is encrypted or on a remote server.
            return await self.storage.async_mailbox(loop,
                api_request['mailbox'],
                username=api_request['username'],
                password=api_request['password'],
                limit=api_request['limit'],
                skip=api_request['skip'])
        watched = False
        info = await load_mailbox()
        return ResponseMailbox(api_request, info, watched)

    async def api_req_search(self, conn_id, access, api_request):
        ctx = api_request['context']
        # Will raise ValueError or NameError if access denied
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_READ)

        loop = asyncio.get_event_loop()
        async def perform_search():
            terms = api_request['terms']
            if isinstance(terms, list):
                terms = ' '.join(terms)
            s_result = await self.search.with_caller(conn_id).async_search(
                loop,
                terms,
                tag_namespace=tag_ns,
                mask_deleted=api_request.get('mask_deleted', True),
                more_terms=scope_s,
                with_tags=(not api_request.get('only_ids', False)))
            if api_request.get('uncooked'):
                return s_result
            s_metadata = (
                await self.metadata.with_caller(conn_id).async_metadata(
                    loop,
                    s_result['hits'],
                    tags=s_result.get('tags'),
                    sort=self.search.SORT_DATE_DEC,  # FIXME: configurable?
                    only_ids=api_request.get('only_ids', False),
                    threads=api_request.get('threads', False),
                    skip=api_request['skip'],
                    limit=api_request['limit'],
                    raw=True))
            s_metadata['metadata'] = list(s_metadata['metadata'])
            return (s_result, s_metadata)

        api_request['skip'] = api_request.get('skip') or 0
        api_request['limit'] = api_request.get('limit', None)
        if self.metadata and self.search:
            results = await perform_search()
            if api_request.get('uncooked'):
                return ResponseSearch(api_request, None, results)
            else:
                only_metadata = results[1]['metadata']
                results[0]['total'] = results[1]['total']
                return ResponseSearch(api_request, only_metadata, results[0])
        else:
            return ResponsePleaseUnlock(api_request)

    async def api_req_counts(self, conn_id, access, api_request):
        ctx = api_request['context']
        # Will raise ValueError or NameError if access denied
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_READ)

        loop = asyncio.get_event_loop()
        async def perform_counts():
            counts = {}
            for terms in api_request['terms_list']:
                result = await self.search.with_caller(conn_id).async_search(
                    loop, terms,
                    tag_namespace=tag_ns,
                    more_terms=scope_s,
                    mask_deleted=api_request.get('mask_deleted', True))
                counts[terms] = dumb_decode(result['hits']).count()
            return counts

        if self.search:
            return ResponseCounts(api_request, await perform_counts())
        else:
            return ResponsePleaseUnlock(api_request)

    async def api_req_tag(self, conn_id, access, api_request):
        ctx = api_request['context']
        # Will raise ValueError or NameError if access denied
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_TAG_RW)

        if self.search:
            loop = asyncio.get_event_loop()
            results = await self.search.with_caller(conn_id).async_tag(loop,
                api_request.get('tag_ops', []),
                tag_namespace=tag_ns,
                more_terms=scope_s,
                tag_undo_id=api_request.get('tag_undo_id'),
                tag_redo_id=api_request.get('tag_redo_id'),
                record_history=api_request.get('undoable', 'Tagging'),
                mask_deleted=api_request.get('mask_deleted', True))

            return ResponseTag(api_request, results)
        else:
            return ResponsePleaseUnlock(api_request)

    async def api_req_email(self, conn_id, access, api_request):
        ctx = api_request.get('context') or self.config.CONTEXT_ZERO
        # Will raise ValueError or NameError if access denied
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_READ)

        # FIXME: Does this user have access to this email?
        #        How will that be determined? Probably a token that
        #        comes from viewing a search result or mailbox?
        #        Seems we should decide that before making any efforts

        loop = asyncio.get_event_loop()
        async def get_email():
            # FIXME: Triage local/remote here? Hmm.
            # Note: This might return a "please login" if the mailbox
            #       is encrypted or on a remote server.
            return await self.storage.with_caller(conn_id).async_email(loop,
                api_request['metadata'],
                text=api_request.get('text', False),
                data=api_request.get('data', False),
                parts=api_request.get('parts', None),
                full_raw=api_request.get('full_raw', False))
        return ResponseEmail(api_request, await get_email())

    async def api_req_contexts(self, conn_id, access, api_request):
        # FIXME: Only return contexts this access level grants use of
        all_contexts = self.config.contexts
        contexts = [all_contexts[k].as_dict() for k in sorted(access.roles)]
        return ResponseContexts(api_request, contexts)

    async def api_req_unlock(self, conn_id, access, api_request):
        if self._is_locked() and api_request.get('passphrase'):
            try:
                self.config.provide_passphrase(api_request['passphrase'])
                self.start_encrypting_workers()
            except PermissionError:
                return ResponsePleaseUnlock(api_request)
        return ResponseUnlocked(api_request)

    async def api_req_changepass(self, conn_id, access, api_request):
        newp = api_request.get('new_passphrase')
        oldp = api_request.get('old_passphrase')
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

    async def api_req_add_to_index(self, conn_id, access, api_request):
        if not (self.metadata and self.search):
            return ResponsePleaseUnlock(api_request)

        with self.config:
            ctx = api_request.get('context') or self.config.CONTEXT_ZERO
            watch = api_request.get('watch')
            acct_id = api_request.get('account')
            context = self.config.get_context(ctx)
            account = None

            # Will raise ValueError or NameError if access denied
            roles, tag_ns, scope_s = access.grants(ctx,
                AccessConfig.GRANT_FS +
                AccessConfig.GRANT_COMPOSE +
                AccessConfig.GRANT_TAG_RW )

            if acct_id:
                account = self.config.get_account(acct_id)

            if watch:
                if not account:
                    section = self._config_new_section('account')
                    self.config[section].update({'name': 'Local mail'})
                    account = self.config.get_account(section)
                    if acct_id and '@' in acct_id:
                        account.addresses.append(acct_id)
                mailbox = api_request['search']['mailbox']
                if mailbox not in account.watched:
                    account.watched.append(mailbox)

        def import_search():
            return self.importer.with_caller(conn_id).import_search(
                api_request['search'],
                api_request.get('initial_tags', []),
                tag_namespace=context.tag_namespace,
                force=api_request.get('force', False),
                full=api_request.get('full', False))

        result = await async_run_in_thread(import_search)
        return ResponsePing(api_request)  # FIXME

    async def api_req_cli(self, conn_id, access, api_request):
        rbuf_cmd = await CLI_COMMANDS[api_request['command']].MsgRunnable(
            self.worker, access, api_request['args'])
        if rbuf_cmd is None:
            return ResponseCLI(api_request, 'text/error', 'No such command')

        rbuf, cmd = rbuf_cmd
        await cmd.web_run()
        rbuf = b''.join(rbuf)
        mimetype = cmd.mimetype.split(';')[0].lower()
        if mimetype in (
                'text/plain', 'text/html', 'text/css', 'text/javascript',
                'application/json'):
            result = str(rbuf, 'utf-8')
        else:
            result = 'base64:' + str(base64.b64encode(rbuf), 'utf-8')
        return ResponseCLI(api_request, mimetype, result)

    async def api_req_openpgp(self, conn_id, access, api_request):
        # OpenPGP requests all follow the same pattern; we figure out which
        # context-worker to use, and forward the request to that one.
        op = api_request['op']
        args = api_request['args']
        kwargs = api_request['kwargs']

        # Will raise ValueError or NameError if access denied
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx,
            AccessConfig.GRANT_TAG_RW)  # FIXME: Grants depend on op?

        loop = asyncio.get_event_loop()
        worker = self._get_openpgp_worker(ctx).with_caller(conn_id)
        result = await worker.async_call(loop, op, *args, qs=kwargs)
        return result

    async def api_request(self, conn_id, access, client_request,
            internal=False):
        try:
            api_request = to_api_request(client_request)
        except KeyError as e:
            logging.warning('Invalid request: %s' % e)
            return {'code': 500}

        # FIXME: This needs refactoring
        result = None
        if type(api_request) == RequestCLI:
            result = await self.api_req_cli(conn_id, access, api_request)
        elif type(api_request) == RequestMailbox:
            result = await self.api_req_mailbox(conn_id, access, api_request)
        elif type(api_request) == RequestSearch:
            result = await self.api_req_search(conn_id, access, api_request)
        elif type(api_request) == RequestTag:
            result = await self.api_req_tag(conn_id, access, api_request)
        elif type(api_request) == RequestCounts:
            result = await self.api_req_counts(conn_id, access, api_request)
        elif type(api_request) == RequestEmail:
            result = await self.api_req_email(conn_id, access, api_request)
        elif type(api_request) == RequestContexts:
            result = await self.api_req_contexts(conn_id, access, api_request)
        elif type(api_request) == RequestAddToIndex:
            result = await self.api_req_add_to_index(conn_id, access, api_request)
        elif type(api_request) == RequestConfigGet:
            result = await self.api_req_config_get(conn_id, access, api_request)
        elif type(api_request) == RequestConfigSet:
            result = await self.api_req_config_set(conn_id, access, api_request)
        elif type(api_request) == RequestUnlock:
            result = await self.api_req_unlock(conn_id, access, api_request)
        elif type(api_request) == RequestChangePassphrase:
            result = await self.api_req_changepass(conn_id, access, api_request)
        elif type(api_request) == RequestSetSecret:
            result = await self.api_req_set_secret(conn_id, access, api_request)
        elif type(api_request) == RequestOpenPGP:
            result = await self.api_req_openpgp(conn_id, access, api_request)
        elif type(api_request) == RequestPing:
            result = ResponsePing(api_request)

        if internal:
            return result

        if result is not None:
            code, json_result = 200, to_json(result)
        else:
            code = 400
            result = {'error': 'Unknown %s' % type(api_request)}
            json_result = to_json(result)

        return {
            'code': code,
            'mimetype': 'application/json',
            'body': bytes(json_result, 'utf-8')}

    def api_req_session(self, access):
        # FIXME: What does this user have access to?
        jsr = AppSessionResource(self, access)
        return {
            'code': 200,
            'mimetype': 'application/json',
            'body': str(jsr)}


    # Internal API

    def choose_user_background(self):
        user_bg_path = os.path.join(self.asset_paths[0], 'backgrounds')
        if os.path.exists(user_bg_path):
            backgrounds = [
                fn for fn in os.listdir(user_bg_path)
                if fn.rsplit('.', 1)[-1] in ('jpg', 'jpeg', 'png')]
            if backgrounds:
                bg = random.choice(backgrounds)
                bg = 'url("/static/backgrounds/%s")' % bg
                self.theme['body_bg'] = bg + ' no-repeat fixed center'

    def apply_theme(self, data):
        self.choose_user_background()
        def _replacer(m):
            key = str(m.group(2), 'utf-8')
            val = bytes(self.theme.get(key, ''), 'utf-8') or m.group(1)
            return b': %s;' % (val,)
        return re.sub(b': +([^\n;]+); +/\* *@(\S+) *\*/', _replacer, data)

    def get_static_asset(self, path, themed=False):
        mimetype = self.EXT_TO_MIME.get(path.rsplit('.', 1)[-1])
        if '..' in path or not mimetype:
            logging.debug('Rejecting path: %s' % path)
            raise ValueError('Naughty path')
        for prefix in self.asset_paths:
            filepath = os.path.join(prefix, path)
            try:
                with open(filepath, 'rb') as fd:
                    data = fd.read()
                    if themed:
                        data = self.apply_theme(data)
                    return {'mimetype': mimetype, 'body': data, 'ttl': 24*3600}
            except:
                pass
        raise OSError('File not found or access denied')

    async def rpc_notify(self, notification, **kwargs):
        only = None
        if kwargs.get('caller'):
            only = kwargs.get('caller')
            if not isinstance(only, list):
                only = [only]
        await self.worker.broadcast(
            ResponseNotification(notification),
            only=only)
        self.worker.reply_json({'ok': 'thanks'}, **kwargs['reply_kwargs'])

    async def rpc_check_result(self, request_id, **kwargs):
        pass

    async def rpc_api(self, request, **kwargs):
        try:
            access = kwargs.get('access')
            if access:
                access = self.config.all_access[access]
            else:
                access = self.config.access_zero()
            rv = await self.api_request(None, access, request, internal=True)
            self.worker.reply_json(rv, **kwargs['reply_kwargs'])
        except:
            logging.exception('rpc_api failed %s' % (request,))
            self.worker.reply_json({'error': 'FIXME'}, **kwargs['reply_kwargs'])

    def rpc_ask_secret(self, context, resource, **kwargs):
        self.worker.reply_json(False, **kwargs['reply_kwargs'])

    def rpc_set_secret(self, context, resource, secret, secret_ttl, **kwargs):
        self.worker.reply_json(False, **kwargs['reply_kwargs'])

    def rpc_session_resource(self, **kwargs):
        jsr = AppSessionResource(self, self.config.access_zero())
        self.worker.reply_json(jsr, **kwargs['reply_kwargs'])

    def rpc_crypto_status(self, **kwargs):
        all_started = self.metadata and self.search and self.importer
        unlocked = self.config.get(
            self.config.SECRETS, 'passphrase', fallback=False) and True
        self.worker.reply_json({
                'encrypted': self.config.has_crypto_enabled,
                'unlocked': unlocked,
                'locked': self._is_locked()},
            **kwargs['reply_kwargs'])

    def rpc_get_access_token(self, **kwargs):
        a0 = self.config.access_zero()
        token, expiration = a0.get_fresh_token()
        self.worker.reply_json({
                'token': token,
                'expires': expiration},
            **kwargs['reply_kwargs'])

