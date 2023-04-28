# TODO: add a status command, to check what is live?
#       add an export command, for exporting messages from A to B

import asyncio
import datetime
import logging
import os
import sys
import time
import traceback

from ...api.requests import *
from ...config import AppConfig, AccessConfig
from ...util.dumbcode import to_json, from_json
from .command import Nonsense, CLICommand


class CommandContext(CLICommand):
    """moggie context [<op> [<name> [options]]]

    This command lists or configures moggie contexts.

    ### Examples

        moggie context list
        moggie context create Yum --with-standard-tags
        moggie context update Yum --tag="icecream" --tag="cake"
        moggie context update Yum --show-tag="flagged"
        moggie context update Yum --remove-tag="cake"
        moggie context update Yum --require="dates:2022"
        moggie context update Yum --forbid="vegetables" --forbid="veggies"
        moggie context remove Yum

    ### Options

    %(OPTIONS)s

    Contexts are used to organize how one or more users may have different
    roles when interacting with a given collection of e-mail and accounts.

    In particular, it is possible to limit which messages are visible to
    the user when they are working in a specific context, either by
    requiring messages have (or lack) specific tags or search terms.

    For a stronger separation of concerns, it is possible to assign a "tag
    namespace" to the context which will prevent messages tagged with
    "in:inbox" within one context, from appearing in "in:inbox" in another.
    """
    __NOTES__ = """

    FIXME: Support --format = `html` and `jhtml`.
    FIXME: Always allow a user to LIST their own contexts; so tweak ROLES
           and the access control strategy. Or make context-list a command
           of its own with no ability to edit thigns?

    """
    NAME = 'context'
    ROLES = AccessConfig.GRANT_ACCESS  # FIXME: Allow user to see own contexts?
    WEBSOCKET = False
    AUTO_START = False
    WEB_EXPOSE = True
    OPTIONS = [[
        ('--format=',      ['text'], 'X=(text*|json)'),
        ('--output=',            [], 'X=(ids|scope), add details to listings'),
        ('--with-standard-tags', [], 'Add common tags to a new context'),
        ('--name=',              [], 'X="Context name"'),
        ('--description=',       [], 'X="Context description"'),
        ('--tag-namespace=',     [], 'X="tag_namespace"'),
        ('--tag=',               [], 'X="tag" (required tag)'),
        ('--show-tag=',          [], 'X="tag" (tag listed in UI)'),
        ('--remove-tag=',        [], 'X="tag" (excluded tag)'),
        ('--scope-search=',      [], 'X="search terms"'),
        ('--require=',           [], 'X="search terms"'),
        ('--forbid=',            [], 'X="search terms"')]]

    def configure(self, args):
        args = self.strip_options(args)
        self.cmd = args[0] if (len(args) > 0) else None
        self.name = args[1] if (len(args) > 1) else None
        if len(args) > 2:
            raise Nonsense('Too many arguments')
        if not self.cmd:
            self.cmd = 'list'
        if self.cmd not in ('list', 'update', 'create', 'remove'):
            raise Nonsense('Unknown command: %s' % self.cmd)
        return []

    async def run(self):
        if self.cmd == 'list':
             result = await self.get_contexts()
        elif self.cmd == 'create':
             result = await self.do_create()
        elif self.cmd == 'update':
             result = await self.do_update()
        elif self.cmd == 'remove':
             result = await self.do_remove()

        fmt = self.options['--format='][-1]
        if fmt == 'json':
            self.emit_json(result)
        else:
            self.emit_text(result)

    def emit_text(self, result):
        fmt0 = '%(k)-13s %(n)-38s'
        fmtN = '%(k)-13s %(n)-13s %(t)-13s %(N)-10s'
        legend = {
            'k': 'KEY',
            'n': 'NAME',
            'd': 'DESCRIPTION',
            't': 'TAGS',
            'i': 'IDS',
            'N': 'NAMESPACE',
            'S': 'SCOPE'}

        want_ids = want_scope = False
        if 'ids' in self.options['--output=']:
            want_ids = True
            fmt0 += ' %(i)-10s'
            fmtN += ' %(i)-10s'
        if 'scope' in self.options['--output=']:
            fmtN += ' %(S)s'
            fmt0 += ' %(S)s'

        self.print(fmtN % legend)
        for ckey, ctx in sorted(list(result.items())):
            d = ctx.get('description', '')
            n = ctx['name']
            if d:
                n = '%s (%s)' % (n, d)

            ns = ctx.get('tag_namespace', '')
            ss = ctx.get('scope_search', '')
            rt = set(ctx.get('tags', []))
            et = set(ctx.get('extra_tags', []))

            ids = sorted(ctx.get('identities', []))
            il = len(ids)
            t = sorted(['%s%s' % (tag, '*' if tag in rt else '')
                        for tag in (rt | et)])
            count = tl = len(t)

            if want_ids:
                count = max(count, il)
            for i in range(0, count+1):
                self.print((fmt0   if (i == 0) else fmtN) % {
                    'k': ckey      if (i == 0) else '',
                    'n': n         if (i == 0) else '',
                    'd': d         if (i == 0) else '',
                    'i': ids[i-1]  if (0 < i <= il) else '',
                    't': t[i-1]    if (0 < i <= tl) else '',
                    'N': ns        if (i > 0) else '',
                    'S': ss        if (i == 0) else ''})

    def emit_json(self, config):
        self.print(to_json(config))

    async def get_contexts(self):
        cfg = await self.worker.async_api_request(self.access,
            RequestConfigGet(contexts=True))

        contexts = cfg['config'].get('contexts', {})
        if self.name:
            for ctx in contexts:
                if self.name in (ctx, contexts[ctx]['name']):
                    self.name = ctx
                    return {ctx: contexts[ctx]}
        else:
            return contexts

        return {}

    def _make_updates(self, current=None):
        updates = []
        opts = self.options

        for opt, var in (
                ('--name=',          'name'),
                ('--description=',   'description'),
                ('--tag-namespace=', 'tag_namespace')):
            if opts[opt]:
                updates.append({
                    'op': 'set',
                    'variable': var,
                    'value': opts[opt][-1]})

        for tag in opts['--remove-tag=']:
            updates.append({
                'op': 'list_del',
                'case_sensitive': False,
                'variable': 'extra_tags',
                'list_val': tag})
            updates.append({
                'op': 'list_del',
                'case_sensitive': False,
                'variable': 'tags',
                'list_val': tag})

        if opts['--with-standard-tags']:
            opts['--show-tag='].extend(AppConfig.STANDARD_CONTAINER_TAGS)
        for var, tags in (
                ('tags',       opts['--tag=']),
                ('extra_tags', opts['--show-tag='])):
            for tag in tags:
                updates.append({
                    'op': 'list_add_unique',
                    'case_sensitive': False,
                    'variable': var,
                    'list_val': tag})

        if (opts['--scope-search=']
                or opts['--forbid='] or opts['--require=']):
            if current and self.name in current:
                scope_search = current[self.name].get('scope_search', '')
            else:
                scope_search = ''
            scope_search = (opts['--scope-search='] or [scope_search])[-1]
            if scope_search in (True, '-'):
                # This is a quirk of our argument parser, the empty string
                # gets represented as True. Fix it!
                scope_search = ''

            for forbid in opts['--forbid=']:
                if scope_search:
                    scope_search += ' '
                scope_search += ' '.join(('-%s' % w) for w in forbid.split())

            for req in opts['--require=']:
                scope_search = ('%s %s' % (req, scope_search)).strip()

            scope_search = scope_search.strip()
            updates.append({
                'op': 'set' if scope_search else 'del',
                'variable': 'scope_search',
                'value': scope_search})

        return updates

        if self.roles in ('-', '', 'none', 'None'):
            return [{
                'op': 'dict_del',
                'variable': 'roles',
                'dict_key': self.context}]
        else:
            # Translate user-friendly role names into role strings
            self.roles = AccessConfig.GRANT_ROLE.get(
                self.roles, [self.roles])[0]
            return [{
                'op': 'dict_set',
                'variable': 'roles',
                'dict_key': self.context,
                'dict_val': self.roles}]

    async def do_create(self):
        current = await self.get_contexts()
        if current:
            raise Nonsense('%s already exists, use update?' % self.name)

        updates = [{
            'op': 'set',
            'variable': 'name',
            'value': self.name}]
        updates.extend(self._make_updates())

        rv = await self.worker.async_api_request(self.access,
            RequestConfigSet(new='context', updates=updates))

        return await self.get_contexts()

    async def do_update(self):
        current = await self.get_contexts()
        if not current:
            raise Nonsense('%s not found, use create?' % self.name)

        updates = self._make_updates(current)
        if not self.name or not updates:
            raise Nonsense('Nothing to do?')

        for akey in current:
            await self.worker.async_api_request(self.access,
                RequestConfigSet(section=akey, updates=updates))
            break

        return await self.get_contexts()

    async def do_remove(self):
        if self._make_updates() or not self.name:
            raise Nonsense('Configure the context or remove it, not both')

        current = await self.get_contexts()
        if not current:
            raise Nonsense('%s not found.' % self.name)

        for akey in current:
            if akey == AppConfig.CONTEXT_ZERO:
                raise Nonsense('%s cannot be removed.' % akey)
            await self.worker.async_api_request(self.access,
                RequestConfigSet(section=akey, updates=[{
                        'op': 'remove_section',
                    }]))
            break

        # FIXME: If the context is referenced by other settings, this
        #        becomes problematic. Refuse? Remove from everywhere?

        return await self.get_contexts()


class CommandUnlock(CLICommand):
    """moggie unlock [<password>]

    This command will unlock a running moggie, granting full access to any
    encrypted content. If no password is supplied, the user will be promted
    to enter one interactively.
    """
    NAME = 'unlock'
    ROLES = AccessConfig.GRANT_ACCESS
    AUTO_START = False

    def configure(self, args):
        self.passphrase = ' '.join(args)
        return []

    def get_passphrase(self):
        if self.passphrase == '-':
            return ''
        elif self.passphrase:
            return self.passphrase
        else:
            import getpass
            return getpass.getpass('Enter passphrase: ')

    async def run(self):
        app_crypto_status = self.worker.call('rpc/crypto_status')
        if not app_crypto_status.get('locked'):
            print('App already unlocked, nothing to do.')
            return True

        self.app.send_json(RequestUnlock(self.get_passphrase()))
        while True:
            msg = await self.await_messages('unlocked', 'notification')
            if msg and msg.get('message'):
                print(msg['message'])
                return (msg['prototype'] == 'unlocked')
            else:
                print('Unknown error (%s) or timed out.' % msg)
                return False


class CommandGrant(CLICommand):
    """moggie grant [<op> [<name> [<roles>] [options]]]

    This command lists or changes what access is currently granted.

    ### Examples

        moggie grant list
        moggie grant list --output=urls
        moggie grant create Bjarni owner --context='Context 0'
        moggie grant update Bjarni user --context='Context 2'
        moggie grant login  Bjarni --ttl=1m
        moggie grant logout Bjarni
        moggie grant remove Bjarni

    ### Options

    %(OPTIONS)s

    Grant lists can be filtered by name or name and context, if a role
    string is also specified then grants will be changed to match.

    Available roles are `owner`, `admin`, `user` and `guest`. The owner
    role has full access to all of Moggie's features and settings, an
    admin is granted control over a specific context, users can use the
    app but not change settings, and guests only have read access.

    (FIXME: We should write in more detail about access roles.)

    The `logout` operation removes all access tokens, rendering any live
    sessions or shared URLs invalid.

    The `login` op will by default create access tokens/URLs which are only
    valid for 1 week. For other durations, specify `--ttl=X` where X can be
    either seconds (no suffix), hours (12h), days (5d), weeks (2w), months
    (1m) or years (10y). Note that months are always a multiple of 31 days,
    and years are always multiples of 365 days.

    Requesting `--output=urls` will change the output to list the URLs
    which are currently live for each user. Requesting `--output=qrcodes`
    will list the URLs and a QR code for each one (or include an SVG
    of the QR code if the output format is JSON).

    Note that QR codes are not generated for localhost/127.0.0.x URLs.
    """
    NAME = 'grant'
    ROLES = AccessConfig.GRANT_ACCESS
    WEBSOCKET = False
    AUTO_START = False
    WEB_EXPOSE = True
    OPTIONS = [[
        ('--context=', ['default'], 'X=(<context-name>|<context-id>)'),
        ('--format=',  ['text'],    'X=(text*|json)'),
        ('--output=',  ['grants'],  'X=(grants*|urls|qrcodes)'),
        ('--ttl=',     [None],      'X=<duration>')]]

    def configure(self, args):
        args = self.strip_options(args)
        self.cmd = args[0] if (len(args) > 0) else None
        self.name = args[1] if (len(args) > 1) else None
        self.roles = args[2] if (len(args) > 2) else None
        if len(args) > 3:
            raise Nonsense('Too many arguments')
        if self.roles and self.options['--context='][-1] == 'default':
            raise Nonsense('Please specify --context=X when granting access')
        if not self.cmd:
            self.cmd = 'list'
        if self.cmd not in ('list', 'update', 'create', 'remove', 'logout', 'login'):
            raise Nonsense('Unknown command: %s' % self.cmd)
        return []

    async def run(self):
        if self.cmd == 'list':
             result = await self.get_roles()
        elif self.cmd == 'create':
             result = await self.do_create()
        elif self.cmd == 'update':
             result = await self.do_update()
        elif self.cmd == 'remove':
             result = await self.do_remove()
        elif self.cmd == 'logout':
             result = await self.do_logout()
        elif self.cmd == 'login':
             result = await self.do_login()

        fmt = self.options['--format='][-1]
        if fmt == 'json':
            self.emit_json(result)
        else:
            self.emit_text(result)

    def emit_text(self, result):
        output = self.options['--output='][-1]
        want_urls = output in ('urls', 'qrcodes')
        if output == 'qrcodes':
            fmt = '%(n)-13s %(e)-10s %(u)s\n%(q)s'
        elif want_urls:
            fmt = '%(k)-13s %(n)-13s %(e)-10s %(u)s'
        else:
            fmt = '%(k)-13s %(n)-13s %(c)15s %(r)10s %(t)s'
        legend = {
            'k': 'KEY',
            'n': 'NAME',
            'c': 'CONTEXT',
            'r': 'ROLE',
            't': 'TOKEN',
            'e': 'EXPIRES',
            'q': 'QRCODE',
            'u': 'URLS'}

        def _fmt_date(ts):
            dt = datetime.datetime.fromtimestamp(int(ts))
            return '%4.4d-%2.2d-%2.2d' % (dt.year, dt.month, dt.day)

        self.print(fmt % legend)
        for ai in result:
            u = ai['urls']
            t = ai['tokens']
            c = sorted(list(ai['contexts'].keys()))
            cl = len(c)
            if want_urls and not u:
                continue
            for i in range(0, max(0, len(u) if want_urls else cl) + 1):
                self.print(fmt % {
                    'k': ai['key']               if (i == 0) else '',
                    'n': ai['name']              if (i == 0) else '',
                    'c': c[i-1]                  if (0 < i <= cl) else '',
                    'r': ai['contexts'][c[i-1]]  if (0 < i <= cl) else '',
                    't': t[0]                    if (t and i == 0) else '',
                    'e': _fmt_date(u[0][0])      if (u and i == 0) else '',
                    'u': u[i][1]                 if (i < len(u)) else '',
                    'q': u[i][2]                 if (i < len(u)) else ''})

    def emit_json(self, config):
        self.print(to_json(config))

    async def get_roles(self, want_context=True):
        cfg = await self.worker.async_api_request(self.access,
            RequestConfigGet(
                urls=True,
                access=True,
                contexts=True))

        result = []
        want_context = want_context and (
            self.options['--context='][-1] != 'default')

        with_tokens = (self.access is True
            or (self.access
                and self.access.config_key == AppConfig.ACCESS_ZERO))

        fmt = self.options['--format='][-1]
        output = self.options['--output='][-1]
        for akey, adata in cfg['config'].get('access', {}).items():
            if self.name in (None, adata['name'], akey):
                if self.name:
                    # Update our idea of what the name is, in the case that
                    # we actually matched on the config section key.
                    self.name = adata['name']
                ctxs = {}
                for ctx, role in adata.get('roles', {}).items():
                    if (not want_context) or (ctx == self.context):
                        ctxs[ctx] = role.strip()

                tokens = sorted([
                        (e, t if with_tokens else '(live)')
                        for t, e in adata.get('tokens', {}).items()],
                    key=lambda i: -int(i[0]))
                urls = []
                if with_tokens and tokens:
                    tok0 = tokens[0]
                    if output == 'qrcodes':
                        import io, pyqrcode
                        def _u(u):
                            url = '%s/@%s/' % (u, tok0[1])
                            if u.startswith('http://127.0.0.'):
                                return (int(tok0[0]), url, '')
                            qc = pyqrcode.create(url, error='L')
                            if fmt == 'text':
                                qc = qc.terminal(quiet_zone=3)
                            else:
                                buf = io.BytesIO()
                                qc.svg(buf)
                                qc = str(buf.getvalue(), 'utf-8')
                            return (int(tok0[0]), url, qc)
                    else:
                        def _u(u):
                            return (int(tok0[0]), '%s/@%s/' % (u, tok0[1]), '')

                    urls.extend(_u(u) for u in cfg['config']['urls'])

                if ctxs:
                    result.append({
                        'key': akey,
                        'name': adata['name'],
                        'urls': urls,
                        'contexts': ctxs,
                        'tokens': [t[1] for t in tokens]})
        return sorted(result, key=lambda r: r['key'])

    def _make_role_update(self):
        if self.roles in ('-', '', 'none', 'None'):
            return [{
                'op': 'dict_del',
                'variable': 'roles',
                'dict_key': self.context}]
        else:
            # Translate user-friendly role names into role strings
            self.roles = AccessConfig.GRANT_ROLE.get(
                self.roles, [self.roles])[0]
            return [{
                'op': 'dict_set',
                'variable': 'roles',
                'dict_key': self.context,
                'dict_val': self.roles}]

    async def do_create(self):
        updates = [{
            'op': 'set',
            'variable': 'name',
            'value': self.name}]
        if self.name and self.roles:
            updates.extend(self._make_role_update())
        if updates:
            current = await self.get_roles(want_context=True)  # Need contexts!
            if current and current[0]['name'] == self.name:
                raise Nonsense('%s already exists, use update?' % self.name)
            rv = await self.worker.async_api_request(self.access,
                RequestConfigSet(new='access', updates=updates))

        return await self.get_roles()

    async def do_update(self):
        updates = []
        if self.name and self.roles:
            updates.extend(self._make_role_update())
        if updates:
            current = await self.get_roles(want_context=False)
            if not (current and current[0]['name'] == self.name):
                raise Nonsense('%s not found, use create?' % self.name)

            akey = current[0]['key']
            if akey == AppConfig.ACCESS_ZERO:
                raise Nonsense(
                    '%s (%s) cannot be changed.' % (self.name, akey))
            rv = await self.worker.async_api_request(self.access,
                RequestConfigSet(section=akey, updates=updates))

        return await self.get_roles()

    async def do_remove(self):
        current = await self.get_roles(want_context=False)
        if not (current and current[0]['name'] == self.name):
            raise Nonsense('%s not found.' % self.name)

        akey = current[0]['key']
        rv = await self.worker.async_api_request(self.access,
            RequestConfigSet(section=akey, updates=[{
                    'op': 'remove_section',
                }]))

        return await self.get_roles()

    async def do_logout(self):
        current = await self.get_roles(want_context=False)
        if not (current and current[0]['name'] == self.name):
            raise Nonsense('%s not found.' % self.name)

        akey = current[0]['key']
        rv = await self.worker.async_api_request(self.access,
            RequestConfigSet(section=akey, updates=[{
                    'op': 'del',
                    'variable': 'tokens'
                }]))

        return await self.get_roles()

    async def do_login(self):
        current = await self.get_roles(want_context=False)
        if not (current and current[0]['name'] == self.name):
            raise Nonsense('%s not found.' % self.name)

        ttl = self.options.get('--ttl=')[-1]
        if ttl:
            ttl = ttl.lower()
            if ttl[-1:] == 'y':
                ttl = int(ttl[:-1]) * 365 * 24 * 3600
            elif ttl[-1:] == 'm':
                ttl = int(ttl[:-1]) * 31 * 24 * 3600
            elif ttl[-1:] == 'w':
                ttl = int(ttl[:-1]) * 7 * 24 * 3600
            elif ttl[-1:] == 'd':
                ttl = int(ttl[:-1]) * 24 * 3600
            elif ttl[-1:] == 'h':
                ttl = int(ttl[:-1]) * 3600
            else:
                ttl = int(ttl)

        akey = current[0]['key']
        rv = await self.worker.async_api_request(self.access,
            RequestConfigSet(section=akey, updates=[{
                    'op': 'new_access_token',
                    'ttl': ttl
                }]))

        return await self.get_roles()


class CommandImport(CLICommand):
    """# moggie import [options] </path/to/mailbox1> [</path/to/mbx2> [...]]

    Scan the named mailboxes for e-mail, adding any found messages to the
    search engine. Re-importing a mailbox will check for updates/changes to
    the contents.

    ### Options

    %(OPTIONS)s

    Importing messages without linking them to any particular account is
    possible, but not recommended since Moggie will be unable to ascertain
    whether the message was "to me" or not.

    Note that using `--watch` will create a new local mail account if there
    is none associated with the active context already.
    """
    NAME = 'import'
    ROLES = (
        AccessConfig.GRANT_FS +
        AccessConfig.GRANT_COMPOSE +
        AccessConfig.GRANT_TAG_RW)
    SEARCH = ('in:incoming',)
    OPTIONS = [[
        ('--context=',  ['default'], 'X=<ctx>, import messages into a specific context'),
        ('--account=',  [None], 'X=<id>, the e-mail or account ID the mail belongs to'),
        ('--username=', [None], 'X=<U>, username required to access the mail (if any)'),
        ('--password=', [None], 'X=<P>, password requried to access the mail (if any)'),
        ('--ifnewer=',      [], 'X=<ts>, ignore files/folders unchanged since timestamp'),
        ('--ignore=',   ['.', '..', 'cur', 'new', 'tmp', '.notmuch'],
                                ''),  # FIXME: Explain?
        ('--recurse',       [], 'Search the named paths recursively for mailboxes'),
        ('--compact',       [], 'Compact the search engine after importing'),
        ('--watch',    [False], 'Watch for new mail in the future'),
        ('--old',           [], 'Treat messages as "old": do not add to inbox etc.'),
        ('--dryrun',        [], 'Test only, adds nothing')]]

    def configure(self, args):
        self.newest = 0
        self.paths = []
        args = self.strip_options(args)
        recurse = bool(self.options['--recurse'])

        newer = 0
        if self.options['--ifnewer=']:
            newer = max(int(i) for i in self.options['--ifnewer='])
        def _is_new(path):
            if not newer:
                return True
            for suffix in ('', os.path.sep + 'cur', os.path.sep + 'new'):
                try:
                    ts = int(os.path.getmtime(path+suffix))
                    self.newest = max(self.newest, ts)
                    if ts > newer:
                        return True
                except (OSError, FileNotFoundError):
                    pass
            return False

        def _recurse(path):
            yield os.path.abspath(path)
            if os.path.isdir(path):
                for p in os.listdir(path):
                    if p not in self.options['--ignore=']:
                        yield from _recurse(os.path.join(path, p))

        for arg in args:
            if arg in self.SEARCH:
                self.paths.append(arg)
            else:
                if not os.path.exists(arg):
                    raise Nonsense('File or path not found: %s' % arg)
                if not os.path.sep in arg:
                    arg = os.path.join('.', arg)
                if recurse:
                    for path in _recurse(arg):
                        if _is_new(path):
                            self.paths.append(path)
                else:
                    fullpath = os.path.abspath(arg)
                    if _is_new(fullpath):
                        self.paths.append(fullpath)

        self.paths.sort()
        return []

    async def run(self):
        from ...config import AppConfig

        requests = []
        for path in self.paths:
            if path in self.SEARCH:
                request_obj = RequestSearch(
                    context=AppConfig.CONTEXT_ZERO,
                    terms=path)
            else:
                request_obj = RequestMailbox(
                    context=AppConfig.CONTEXT_ZERO,
                    username=self.options['--username='][-1],
                    password=self.options['--password='][-1],
                    mailbox=path)

            requests.append((path, RequestAddToIndex(
                context=AppConfig.CONTEXT_ZERO,
                search=request_obj,
                account=self.options['--account='][-1],
                watch=self.options['--watch'][-1],
                force=(path in self.SEARCH))))

        if not requests:
            return True

        if self.options['--dryrun']:
            for r in requests:
                print('import %s' % (r[0],))
            return True

        def _next():
            path, request_obj = requests.pop(0)
            sys.stdout.write('[import] Processing %s\n' % path)
            self.worker.api_request(True, request_obj)

        _next()
        while True:
            try:
                msg = await self.await_messages('notification', timeout=120)
                if msg and msg.get('message'):
                    sys.stdout.write('\33[2K\r' + msg['message'])
                    if msg.get('data', {}).get('pending') == 0:
                        sys.stdout.write('\n')
                        if requests:
                            _next()
                        else:
                            if self.options['--compact']:
                                self.metadata_worker().compact(full=True)
                                self.search_worker().compact(full=True)
                            return True
                else:
                    print('\nUnknown error (%s) or timed out.' % msg)
                    return False
            except (asyncio.CancelledError, KeyboardInterrupt):
                if requests:
                    print('\n[CTRL+C] Will exit after this import. Interrupt again to force quit.')
                    requests = []
                else:
                    print('\n[CTRL+C] Exiting. Running imports may complete in the background.')
                    return False
            except:
                logging.exception('Woops')
                raise
        return True


## FIXME - Are these still what we want/need? ##


class CommandWelcome(CLICommand):
    """moggie welcome

    This command displays either a login page or welcomes the user to
    the app. It is not useful from the command-line.
    """
    NAME = 'welcome'
    ROLES = None
    WEBSOCKET = False
    AUTO_START = False
    WEB_EXPOSE = True

    async def run(self):
        self.print_html_start()
        try:
            asset = self.worker.app.get_static_asset('html/welcome.html')
            self.print(str(asset['body'], 'utf-8'))
        except Exception as e:
            self.print('<pre>Failed: %s</pre>' % traceback.format_exc())
        self.print_html_end()


def CommandEnableEncryption(wd, args):
    from ...config import AppConfig
    import getpass
    cfg = AppConfig(wd)
    try:
        if cfg.has_crypto_enabled:
            print('Enter a passphrase verify you can decrypt your config.')
        else:
            print('Enter a passphrase to encrypt sensitive app data. Note')
            print('this cannot currently be undone. Press CTRL+C to abort.')
        print()
        p1 = getpass.getpass('Enter passphrase: ')
        p2 = getpass.getpass('Repeat passphrase: ')
        print()
        if p1 != p2:
            return print('Passphrases did not match!')

        if cfg.has_crypto_enabled:
            ct = None
        else:
            print('To enable password/passphrase recovery on this data, in')
            print('case you forget your passphrase, enter one more emails.')
            print('Leave blank to disable recovery (dangerous!).')
            ct = [e for e in
                input('Recovery e-mails: ').replace(',', ' ').split()
                if e]
            if not ct:
                cfg.set(cfg.GENERAL, 'recovery_svc_disable', 'True')
            else:
                print('\nVery good, will enable recovery via %s\n'
                    % (', '.join(ct),))

                raise Nonsense('FIXME: This is not implemented')

        cfg.provide_passphrase(p1, contacts=ct)
        if cfg.has_crypto_enabled:
            print('Great, that passphrase works!')
        else:
            cfg.generate_master_key()
            print('Encryption enabled, good job!')

    except PermissionError as e:
        print('# oops: %s' % e)


def CommandConfig(wd, args):
    from ...config import AppConfig
    cfg = AppConfig(wd)
    if len(args) < 1:
        print('%s' % cfg.filepath)

    elif args[0] == 'get':
        section = args[1]
        options = args[2:]
        if not options:
            options = cfg[section].keys()
        print('[%s]' % (section,))
        for opt in options:
            try:
                print('%s = %s' % (opt, cfg[section][opt]))
            except KeyError:
                print('# %s = (unset)' % (opt,))

    elif args[0] == 'set':
        try:
            section, option, value = args[1:4]
            cfg.set(section, option, value, save=True)
            print('[%s]\n%s = %s' % (section, option, cfg[section][option]))
        except KeyError:
            print('# Not set: %s / %s' % (section, option))
