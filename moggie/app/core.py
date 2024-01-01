import asyncio
import base64
import copy
import logging
import multiprocessing
import os
import random
import re
import threading
import traceback
import time
import sys

from moggie import Moggie
from ..config import APPNAME_UC, APPVER, AppConfig, AccessConfig
from ..config.helpers import DictItemProxy, EncodingListItemProxy
from ..api.requests import *
from ..api.responses import *
from ..api.exceptions import *
from ..util.asyncio import async_run_in_thread
from ..util.dumbcode import *
from ..workers.importer import ImportWorker
from ..workers.metadata import MetadataWorker
from ..workers.storage import StorageWorkers
from ..workers.search import SearchWorker
from .cli import CLI_COMMANDS
from .cron import Cron


def _b(p):
    return bytes(p, 'utf-8') if isinstance(p, str) else p

def _u(p):
    try:
        return str(p, 'utf-8') if isinstance(p, bytes) else p
    except UnicodeDecodeError:
        return p

def safe_str(p):
    try:
        return str(p, 'utf-8') if isinstance(p, bytes) else p
    except UnicodeDecodeError:
        return dumb_encode_asc(p)


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

    DEFAULT_CRONTAB = """\
# This is the schedule for moggie updates, checking mail, unsnoozing
# snoozed messages, things like that.
#
# This file uses a very similar format to crontab(5). Commands can be
# internal moggie API calls, shell commands, or python one-liners.
# This file is automatically reloaded by moggie if changed.
#
### Checking for new mail:
#
*/2  * * * *  moggie new --only-inboxes
*/30 * * * *  moggie new
#
### Retraining the autotaggers (spam filters)
#
*/15 * * * *  moggie autotag-train
01   * * * *  moggie autotag-train in:junk -- in:read (version:recent OR dates:recent)
07  12 * * 1  moggie autotag-train --compact
#
### Un-hide snoozed e-mails, mark as urgent
#
00  8 * * *   no-skip: moggie tag +urgent -hidden -_mp_z%(yyyy_mm_dd)s -- in:hidden in:_mp_z%(yyyy_mm_dd)s
"""

    def __init__(self, app_worker):
        self.work_dir = os.path.normpath(# FIXME: This seems a bit off
            os.path.join(app_worker.worker_dir, '..'))

        self.worker = app_worker
        self.config = AppConfig(self.work_dir)
        self.moggie = Moggie(app=self, app_worker=app_worker, access=True)

        self.rpc_functions = {
            b'rpc/notify':            (True, self.rpc_notify),
            b'rpc/api':               (True, self.rpc_api),
            b'rpc/ask_secret':        (True, self.rpc_ask_secret),
            b'rpc/set_secret':        (True, self.rpc_set_secret),
            b'rpc/crypto_status':     (True, self.rpc_crypto_status)}

        self.counter = int(time.time()) - 1663879773
        self.metadata = None
        self.importer = None
        self.storage = None
        self.openpgp_workers = {}
        self.stores = {}
        self.search = None
        self.cron = None
        self.crontab_internal = "*/5 * * * *  app.load_crontab()"
        self.crontab_last_loaded = 0

        # FIXME: Make this customizable somehow
        self.theme = {'_unused_body_bg': '#fff'}
        self.asset_paths = [
            os.path.join(self.work_dir, 'assets'),
            os.path.normpath(os.path.join(
                os.path.dirname(__file__), '..', '..', 'assets'))]

    # Lifecycle

    def load_crontab(self):
        crontab_fn = os.path.join(self.work_dir, 'crontab')
        try:
            if not os.path.exists(crontab_fn):
                with open(crontab_fn, 'w') as fd:
                    fd.write(self.DEFAULT_CRONTAB)

            cron_mtime = os.path.getmtime(crontab_fn)
            if cron_mtime != self.crontab_last_loaded:
                # Mark that we tried, even if we then fail here below
                self.crontab_last_loaded = cron_mtime
                with open(crontab_fn, 'r') as fd:
                    self.cron.parse_crontab(fd.read())
                logging.info('[crontab] Loaded from %s' % crontab_fn)
        except Exception as e:
            logging.exception('[crontab] Read failed: %s' % crontab_fn)

    async def tick(self, log_attrs):
        if self.storage:
            self.storage.housekeeping()  # Reaps zombies

        log_attrs['workers'] = 1 + len(multiprocessing.active_children())
        del log_attrs['mem_free']
        logging.info('[app] Still alive! '
            + '; '.join('%s=%s' % pair for pair in log_attrs.items()))

        if self.cron:
            await self.cron.async_run_scheduled()

    def _get_openpgp_worker(self, ctx):
        from moggie.workers.openpgp import OpenPGPWorker

        context = self.config.contexts[ctx]
        worker_name = OpenPGPWorker.KIND
        if context.tag_namespace:
            worker_name += '-' + context.tag_namespace

        if worker_name in self.openpgp_workers:
            if self.openpgp_workers[worker_name].exitcode is not None:
                self.openpgp_workers[worker_name].join()
                del self.openpgp_workers[worker_name]

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
                log_level=log_level,
                shutdown_idle=120)
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

        # FIXME: If our metadata or storage workers fail to start, we should
        #        abort and notify the user that formats have changed and they
        #        need to nuke their data. This should NEVER happen after we go
        #        beta.

        if self.cron is None:
            self.cron = Cron(self.moggie, aes_keys, eval_env={
                'moggie': self.moggie,
                'config': self.config,
                'app': self})
            self.cron.parse_crontab(self.crontab_internal, source='app.core')
            self.load_crontab()

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

        return True

    def start_workers(self, start_encrypted=True):
        notify_url = self.worker.callback_url('rpc/notify')
        log_level = self.worker.log_level

        if start_encrypted:
            if not self.config.has_crypto_enabled:
                from moggie.crypto.passphrases import generate_passcode
                logging.info(
                    '[app] Generating initial (unlocked) encryption keys.')
                passphrase = generate_passcode(groups=5)
                self.config[self.config.SECRETS]['passphrase'] = passphrase
                self.config.provide_passphrase(passphrase, fast=True)
                self.config.generate_master_key()

            if not self.start_encrypting_workers():
                logging.warning(
                    '[app] Failed to start encrypting workers. Need login?')

        self.storage = StorageWorkers(self.worker.worker_dir,
            metadata=self.metadata,
            ask_secret=self._fs_ask_secret,
            set_secret=self._fs_set_secret,
            notify=notify_url,
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

        multiprocessing.active_children()
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
        if hasattr(secret, 'get_passphrase_bytes'):
            secret = secret.get_passphrase_bytes()
        return self.worker.call('rpc/set_secret',
            context, resource, secret, ttl,
            hide_qs=True)  # Keep secrets out of web logs

    def _is_locked(self):
        return (self.config.has_crypto_enabled and not self.config.aes_key)

    def _config_new_section(self, create, config=None):
        config = config or self.config
        while True:
            section = '%s%x' % ({
                    'access': config.ACCESS_PREFIX,
                    'account': config.ACCOUNT_PREFIX,
                    'context': config.CONTEXT_PREFIX
                }[create], self.counter % 0x10000)
            self.counter += 1
            if section not in config:
                break
        return section

    def _remember_credentials(self, conn_id, access, api_req):
        try:
            ctx = api_req.get('context') or self.config.CONTEXT_ZERO
            roles, _, _ = access.grants(ctx, AccessConfig.GRANT_ACCESS)
        except (NameError, ValueError):
            # FIXME: Generate a Notification so use sees something?
            logging.error(
                'Save credentials request denied for %s, %s, %s.'
                % (conn_id, access, ctx))
            return

        context = self.config.get_context(ctx)
        for res, keys in (api_req.get('remember_credentials') or {}).items():
            creds = dict((k, api_req[k]) for k in keys)
            context.set_secret(res, creds)
            logging.info('Saved credentials in %s for %s' % (ctx, res))

    def _get_saved_credentials(self, api_req, resource):
        resource = _u(resource)
        ctx = api_req.get('context') or self.config.CONTEXT_ZERO
        context = self.config.get_context(ctx)
        sep = '/' if resource.startswith('imap:') else os.path.sep
        parts = resource.split(sep)
        while parts:
            creds = context.get_secret(sep.join(parts))
            if creds:
                return creds
            else:
                parts.pop(-1)
        return None

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
        logging.info('[api/config] %s [%s] for %s' % (
            'Creating' if create else 'Updating', section, access.name))

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
                logging.info('[api/config] error: %s' % errors[-1])

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
            deep = (api_request['contexts'] == api_request.DEEP)
            contexts = self.config.contexts
            if which and which.startswith(self.config.CONTEXT_PREFIX):
                if access.grants(which, AccessConfig.GRANT_ACCESS):
                    result['contexts'] = {
                        which: contexts[which].as_dict(deep=deep)}
                else:
                    error = 'Access denied: contexts/%s' % (which,)
            elif access.grants(czero, AccessConfig.GRANT_ACCESS):
                result['contexts'] = dict(
                    (k, v.as_dict(deep=deep)) for k,v in contexts.items())
            else:
                error = 'Access denied: contexts'

        return ResponseConfigGet(api_request, result, error=error)

    async def api_req_browse(self, conn_id, access, api_request):
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx,
            AccessConfig.GRANT_READ + AccessConfig.GRANT_FS)

        # FIXME: Triage local/remote here? Access controls!!

        # Note: This might return a "please login" if the mailbox
        #       is encrypted or on a remote server.
        loop = asyncio.get_event_loop()
        path = api_request['path']
        result = []
        if path is True:
            from ..config.paths import mail_path_suggestions
            paths = mail_path_suggestions(
                config=self.config, context=ctx, local=True)

            # FIXME: Security and access controls, please?

            for suggest in paths:
                path = suggest['path']
                if path.startswith('imap:'):
                    info = {'path': path}
                    if len(path.split('/')) > 3:
                        info['magic'] = ['imap']
                else:
                    info = await self.storage.async_info(loop,
                        path, details=True, recurse=0, relpath=False,
                        username=api_request.get('username'),
                        password=api_request.get('password'))
                if 'src' in info:
                    del info['src']
                suggest.update(info)
                result.append(suggest)
        else:
            info = await self.storage.async_info(loop,
                path, details=True, recurse=1, relpath=False,
                username=api_request.get('username'),
                password=api_request.get('password'))

            children = []
            if 'contents' in info:
                children = info['contents']
                del info['contents']

            result = [info] + children

        # Augment results with any configured path policies
        context = self.config.contexts[ctx]
        pmap = dict((_b(i['path']), i) for i in result)
        paths = list(pmap.keys())
        for p, pol in context.get_path_policies(paths, inherit=False).items():
            pmap[p]['policy'] = pol
        for p, pol in context.get_path_policies(paths).items():
            pmap[p]['policy_full'] = pol

        return ResponseBrowse(api_request, result)

    async def api_req_mailbox(self, conn_id, access, api_request):
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx,
            AccessConfig.GRANT_READ + AccessConfig.GRANT_FS)

        # FIXME: Triage local/remote here? Access controls!!

        terms = api_request['terms']
        if terms:
            term_list = terms.strip().split()
            is_indexed = 'is:indexed' in term_list
            is_unindexed = 'is:unindexed' in term_list
            terms = ' '.join(term for term in term_list
                if term not in ('is:indexed', 'is:unindexed')) or None
        else:
            is_indexed = is_unindexed = False

        # FIXME: Limit and skip are nonsensical if there are multiple
        #        mailboxes, but also if augmentation adds/removes things.
        # A nicer UX might be for this to become a background job with
        # a wall-clock deadline, where we keep collecting results which
        # the user can access by re-calling this method? We can then
        # notify the user that more results are available and pause until
        # they have acked and loaded the results.
        info = []
        loop = asyncio.get_event_loop()
        for mailbox in (api_request.get('mailboxes') or []):
            info.extend(await self.storage.async_mailbox(loop, mailbox,
                        terms=terms,
                        username=api_request['username'],
                        password=api_request['password'],
                        limit=api_request['limit'],
                        skip=api_request['skip']))

        info = await self.metadata.with_caller(conn_id).async_augment(
            loop, info,
            threads=api_request.get('threads', False),
            only_indexed=is_indexed,
            only_unindexed=is_unindexed)

        watched = False
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
                more_terms=scope_s,
                mask_deleted=api_request.get('mask_deleted', True),
                mask_tags=api_request.get('mask_tags'),
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

        if not self.search:
            return ResponsePleaseUnlock(api_request)

        loop = asyncio.get_event_loop()

        tag_op_sets = api_request.get('tag_ops', [])
        for i, tagops_hits_mailboxes in enumerate(tag_op_sets):
            if len(tagops_hits_mailboxes) > 2:
                tagops, hits, mailboxes = tagops_hits_mailboxes
                if mailboxes:
                    if not isinstance(hits, str):
                        raise ValueError('Incompatible arguments')

                    r1 = await self.api_req_mailbox(conn_id, access,
                        RequestMailbox(
                            context=ctx,
                            mailboxes=mailboxes,
                            username=api_request.get('username', None),
                            password=api_request.get('password', None),
                            limit=None,
                            terms=hits))

                    md = self.metadata
                    r2 = await md.with_caller(conn_id).async_add_metadata(
                        loop, r1['emails'], update=True)
                    hits = r2['added'] + r2['updated']
                    logging.info(
                        '[api/tag] Added %d messages to index' % len(hits))

                tag_op_sets[i] = (tagops, hits)

        results = await self.search.with_caller(conn_id).async_tag(loop,
            tag_op_sets,
            tag_namespace=tag_ns,
            more_terms=scope_s,
            tag_undo_id=api_request.get('tag_undo_id'),
            tag_redo_id=api_request.get('tag_redo_id'),
            record_history=api_request.get('undoable', 'Tagging'),
            mask_deleted=api_request.get('mask_deleted', True))

        return ResponseTag(api_request, results)

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
                full_raw=api_request.get('full_raw', False),
                username=api_request.get('username'),
                password=api_request.get('password'))

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
                logging.exception('[api/changepass] Change Passphrase failed')
            finally:
                self.start_workers()
        return None

    async def api_req_paths(self, conn_id, access, api_request):
        if not (self.metadata and self.search):
            return ResponsePleaseUnlock(api_request)

        import_only = api_request.get('import_only', False)
        config_only = api_request.get('config_only', False)
        if config_only and import_only:
            raise ValueError('Config-only, or import-only - not both!')

        requests = api_request.get('policies', [api_request])
        for req in requests:
            if req['context'] != api_request['context']:
                raise PermissionError('Contexts must all match.')
            if req.get('import_only', False) != import_only:
                raise PermissionError('Import-only rules must all match.')
            if req.get('config_only', False) != config_only:
                raise PermissionError('Config-only rules must all match.')

        if import_only:
            # This lets us use all the existing config and logic, without
            # risking anything being persisted.
            config = self.config.get_ephemeral_snapshot()
        else:
            config = self.config

        ctx = api_request.get('context') or config.CONTEXT_ZERO
        context = config.get_context(ctx)

        # Will raise ValueError or NameError if access denied
        roles, tag_ns, scope_s = access.grants(ctx,
            AccessConfig.GRANT_FS +
            AccessConfig.GRANT_COMPOSE +
            AccessConfig.GRANT_TAG_RW )

        with config:
            for req in requests:
                if not req['path']:
                    logging.exception('[api/paths] Path is required: %s' % req)
                    raise ValueError('Path is required')
                context.set_path(req['path'],
                    label=req.get('label'),
                    account=req.get('account'),
                    watch_policy=req.get('watch_policy'),
                    copy_policy=req.get('copy_policy'),
                    tags=req.get('tags'),
                    _remove=(not import_only))

            for req in requests:
                path = _b(req['path'])
                if path.startswith(b'imap:'):
                    policy = context.get_path_policies(path, slim=False)[path]
                    acct_id = policy['account']
                    if acct_id and not config.get_account(acct_id):
                        logging.info('Auto-creating account: %s' % acct_id)
                        section = self._config_new_section('account', config)
                        config[section].update({'name': acct_id})
                        account = config.get_account(section)
                        if '@' in acct_id:
                            account.addresses.append(acct_id)
                        context.accounts.append(section)

        if not config_only:
            paths = [req['path'] for req in requests]
            return await self.api_req_import(conn_id, access, api_request,
                config=config, ctx=ctx, paths=paths)

        return ResponsePing(api_request)  # FIXME

    async def api_req_import(self, conn_id, access, api_request,
            config=None, ctx=None, paths=None):
        if not (self.metadata and self.search):
            return ResponsePleaseUnlock(api_request)

        compact = api_request.get('compact')
        only_inboxes = api_request.get('only_inboxes')
        import_full = api_request.get('import_full')
        paths = paths or api_request.get('paths') or []
        config = config or self.config

        ctx = ctx or api_request.get('context') or config.CONTEXT_ZERO
        context = config.get_context(ctx)  # Might be temporary
        loop = asyncio.get_event_loop()

        if not paths and type(api_request) == RequestPathImport:
            pols = context.get_path_policies()
            if only_inboxes:
                _tags = lambda p: pols[p].get('tags', [])
                paths = [p for p in pols if 'inbox' in _tags(p)]
                logging.debug('[api/import] Configured Inboxes in %s: %s'
                    % (ctx, paths))
            else:
                paths = list(pols.keys())
                logging.debug('[api/import] Configured paths in %s: %s'
                    % (ctx, paths))
        else:
            logging.debug('[api/import] Requested paths: %s' % paths)

        req_creds = dict((k, api_request[k])
            for k in ('username', 'password') if k in api_request)
        if req_creds:
            credmap = dict((_b(p), req_creds) for p in paths)
        else:
            credmap = dict(
                (_b(p), self._get_saved_credentials(api_request, p) or {})
                for p in paths)

        results = {}
        to_import = {}
        recursed = set()
        while paths:
            path = _b(paths.pop(0))
            creds = credmap[path]
            try:
                policy = context.get_path_policies(path, slim=False)[path]

                rec = 1 if policy['watch_policy'] in ('watch', 'sync') else 0
                result = await self.storage.async_info(loop,
                    path, details=True, recurse=rec, relpath=False, **creds)
                if not result:
                    logging.debug(
                        '[api/import] Failed to get info for %s' % path)
                    continue

                contents = result.get('contents') or []
                if 'contents' in result:
                    del result['contents']

                for r in [result] + contents:
                    rpath = _b(r['path'])
                    if (r is not result) and r.get('is_dir'):
                        if (rpath not in paths) and (rpath != path):
                            paths.append(rpath)
                    if r.get('magic'):
                        ppol = context.get_path_policies(rpath).get(rpath, {})
                        if only_inboxes and 'inbox' not in ppol['tags']:
                            logging.debug(
                                '[api/import] Not an Inbox: %s/%s' % (r, ppol))
                            continue
                        elif (not import_full
                                and 'mtime' in r
                                and ppol.get('updated')):
                            if int(ppol['updated'], 16) >= r['mtime']:
                                logging.debug(
                                    '[api/import] Unchanged: %s' % (r['path'],))
                                results[safe_str(rpath)] = {'unchanged': True}
                                continue
                        to_import[rpath] = ppol
                        credmap[rpath] = creds
            except NeedInfoException:
                raise
            except:
                logging.exception('[api/import] Failed to check path %s' % path)

        def _import_path(context, req, path, policy, compact_now):
            policy_tags = (policy.get('tags') or '').split(',')
            return self.importer.with_caller(conn_id).import_search(
                RequestMailbox(
                    context=ctx,
                    mailbox=path,
                    limit=None,
                    **credmap[path]),
                policy_tags,
                tag_namespace=context.tag_namespace,
                compact=compact_now,
                full=True)

        plan = sorted(list(to_import.items()))
        for i, (path, ppol) in enumerate(plan):
            logging.debug('[api/import] Importing: %s with %s' % (path, ppol))
            update_time = int(time.time())
            results[safe_str(path)] = await async_run_in_thread(
                _import_path, context, api_request, path, ppol,
                compact and (i+1 == len(plan)))
            context.set_path_updated(path, '%x' % update_time)

        return ResponsePathImport(api_request, ctx, results)

    async def api_req_cli(self, conn_id, access, api_req):
        args = copy.copy(api_req['args'])
        for k in ('username', 'password'):
            v = api_req.get(k)
            if v is not None:
                args.insert(0, '--%s=%s' % (k, v))

        rbuf_cmd = await CLI_COMMANDS.get(api_req.command).MsgRunnable(
            Moggie(access=access, app=self, app_worker=self.worker), args)
        if rbuf_cmd is None:
            return ResponseCommand(api_req, 'text/error', 'No such command')

        rbuf, cmd = rbuf_cmd
        await cmd.web_run()

        mimetype = cmd.mimetype.split(';')[0].lower()
        if mimetype == 'application/moggie-internal':
            result = rbuf[:-1]
        elif mimetype.startswith('text/') or mimetype == 'application/json':
            result = str(b''.join(rbuf), 'utf-8')
        else:
            result = b''.join(rbuf)

        return ResponseCommand(api_req, mimetype, result)

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
        result = await worker.async_call(loop, op, *args, qs=kwargs, hide_qs=True)
        return result

    async def api_req_autotag(self, conn_id, access, api_request):
        # Will raise ValueError or NameError if access denied
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_TAG_RW)
        loop = asyncio.get_event_loop()
        return await self.importer.with_caller(conn_id).async_call(
            loop, 'autotag',
            tag_ns, api_request['tags'], api_request['search'])

    async def api_req_autotag_train(self, conn_id, access, api_request):
        # Will raise ValueError or NameError if access denied
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx,
            AccessConfig.GRANT_TAG_RW + AccessConfig.GRANT_TAG_X)
        loop = asyncio.get_event_loop()
        return await self.importer.with_caller(conn_id).async_call(
            loop, 'autotag_train',
            tag_ns,
            api_request['tags'],
            api_request['search'],
            api_request['compact'])

    async def api_req_autotag_classify(self, conn_id, access, api_request):
        # Will raise ValueError or NameError if access denied
        ctx = api_request['context']
        roles, tag_ns, scope_s = access.grants(ctx, AccessConfig.GRANT_TAG_RW)
        loop = asyncio.get_event_loop()
        return await self.importer.with_caller(conn_id).async_call(
            loop, 'autotag_classify',
            tag_ns, api_request['tags'], api_request['keywords'])

    async def _route_api_request(self, conn_id, access, api_req):
        # FIXME: This needs refactoring
        result = None
        if access is True:
            access = self.config.access_zero()
        if type(api_req) == RequestCommand:
            result = await self.api_req_cli(conn_id, access, api_req)
        elif type(api_req) == RequestCounts:
            result = await self.api_req_counts(conn_id, access, api_req)
        elif type(api_req) == RequestSearch:
            result = await self.api_req_search(conn_id, access, api_req)
        elif type(api_req) == RequestTag:
            result = await self.api_req_tag(conn_id, access, api_req)
        elif type(api_req) == RequestEmail:
            result = await self.api_req_email(conn_id, access, api_req)
        elif type(api_req) == RequestMailbox:
            result = await self.api_req_mailbox(conn_id, access, api_req)
        elif type(api_req) == RequestBrowse:
            result = await self.api_req_browse(conn_id, access, api_req)
        elif type(api_req) == RequestContexts:
            result = await self.api_req_contexts(conn_id, access, api_req)
        elif type(api_req) in (RequestPathPolicy, RequestPathPolicies):
            result = await self.api_req_paths(conn_id, access, api_req)
        elif type(api_req) == RequestPathImport:
            result = await self.api_req_import(conn_id, access, api_req)
        elif type(api_req) == RequestConfigGet:
            result = await self.api_req_config_get(conn_id, access, api_req)
        elif type(api_req) == RequestConfigSet:
            result = await self.api_req_config_set(conn_id, access, api_req)
        elif type(api_req) == RequestUnlock:
            result = await self.api_req_unlock(conn_id, access, api_req)
        elif type(api_req) == RequestChangePassphrase:
            result = await self.api_req_changepass(conn_id, access, api_req)
        elif type(api_req) == RequestSetSecret:
            result = await self.api_req_set_secret(conn_id, access, api_req)
        elif type(api_req) == RequestOpenPGP:
            result = await self.api_req_openpgp(conn_id, access, api_req)
        elif type(api_req) == RequestAutotag:
            result = await self.api_req_autotag(conn_id, access, api_req)
        elif type(api_req) == RequestAutotagTrain:
            result = await self.api_req_autotag_train(conn_id, access, api_req)
        elif type(api_req) == RequestAutotagClassify:
            result = await self.api_req_autotag_classify(conn_id, access, api_req)
        elif type(api_req) == RequestPing:
            result = ResponsePing(api_req)
        return result

    async def api_request(self, conn_id, access, client_request,
            internal=False):
        try:
            api_req = to_api_request(client_request)
        except KeyError as e:
            logging.warning('[api] Invalid request: %s' % e)
            return {'code': 500}

        try:
            who = 'internal' if access is True else access.name
            if type(api_req) == RequestPing:
                pass
            else:
                what = api_req['req_type'] + ('%s' % (api_req.get('args', ''),))
                if len(what) > 256:
                    what = what[:254] + '..'
                fmt = '[api] %s/%s requested %s'
                logging.info(fmt % (who, (conn_id or 'internal'), what))
            try:
                result = await self._route_api_request(conn_id, access, api_req)

                # If we get this far, the method succeeded. Did it have access
                # credentials it wants us to remember?
                if 'remember_credentials' in api_req:
                    self._remember_credentials(conn_id, access, api_req)

            except NeedInfoException as e:
                update, resource = None, e.exc_data.get('resource')
                if resource:
                    update = self._get_saved_credentials(api_req, resource)
                if not update:
                    raise
                logging.debug(
                    'Retrying with saved credentials for %s' % (resource,))
                api_req.update(update)
                result = await self._route_api_request(conn_id, access, api_req)

        except APIException as exc:
            result = error = exc.as_dict()
            error['error'] = str(exc)
            error['request'] = api_req
            del error['traceback']
            logging.debug('[api] Returning error %s: %s %s'
                % (exc.__class__.__name__, error['error'], error['exc_data']))
        except PermissionError as exc:
            result = error = APIAccessDenied().as_dict()
            error['error'] = str(exc)
            error['request'] = api_req
            del error['traceback']
            logging.debug('[api] Returning error %s: %s %s'
                % (exc.__class__.__name__, error['error'], error['exc_data']))

        if internal:
            return result

        if result is not None:
            code, json_result = 200, to_json(result)
        else:
            code = 400
            result = {'error': 'Unknown %s' % type(api_req)}
            json_result = to_json(result)

        return {
            'code': code,
            'mimetype': 'application/json',
            'body': bytes(json_result, 'utf-8')}


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
            logging.debug('[assets] Rejecting path: %s' % path)
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

    async def rpc_api(self, request, **kwargs):
        try:
            access = kwargs.get('access')
            if access:
                access = self.config.all_access[access]
            else:
                # FIXME: Understand and document why this is safe
                access = self.config.access_zero()
            rv = await self.api_request(None, access, request, internal=True)
            self.worker.reply_json(rv, **kwargs['reply_kwargs'])
        except:
            logging.exception('[api/rpc] Failed %s' % (request,))
            self.worker.reply_json({'error': 'FIXME'}, **kwargs['reply_kwargs'])

    def rpc_ask_secret(self, context, resource, **kwargs):
        self.worker.reply_json(False, **kwargs['reply_kwargs'])

    def rpc_set_secret(self, context, resource, secret, secret_ttl, **kwargs):
        self.worker.reply_json(False, **kwargs['reply_kwargs'])

    def rpc_crypto_status(self, **kwargs):
        all_started = self.metadata and self.search and self.importer
        unlocked = self.config.get(
            self.config.SECRETS, 'passphrase', fallback=False) and True
        self.worker.reply_json({
                'encrypted': self.config.has_crypto_enabled,
                'unlocked': unlocked,
                'locked': self._is_locked()},
            **kwargs['reply_kwargs'])
