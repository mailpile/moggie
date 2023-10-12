# TODO: add a status command, to check what is live?
#       add an export command, for exporting messages from A to B

import asyncio
import copy
import datetime
import logging
import os
import sys
import time
import traceback

from ...api.requests import *
from ...config import AppConfig, AccessConfig
from ...util.dumbcode import to_json, from_json
from ...util.friendly import *
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
        ('--output=',            [], 'X=(accounts|ids|identities|scope), add details to listings'),
        ('--with-standard-tags', [], 'Add common tags to a new context'),
        ('--name=',              [], 'X="Context name"'),
        ('--description=',       [], 'X="Context description"'),
        ('--tag-namespace=',     [], 'X="tag_namespace"'),
        ('--tag=',               [], 'X="tag" (required tag)'),
        ('--show-tag=',          [], 'X="tag" (tag listed in UI)'),
        ('--remove-tag=',        [], 'X="tag" (excluded tag)'),
        ('--scope-search=',      [], 'X="search terms"'),
        ('--require=',           [], 'X="search terms"'),
        ('--forbid=',            [], 'X="search terms"'),
        ('--context=',           [], None)]]

    def configure(self, args):
        args = self.strip_options(args)
        self.cmd = args[0] if (len(args) > 0) else None
        self.name = args[1] if (len(args) > 1) else None
        if len(args) > 2:
            raise Nonsense('Too many arguments')
        if not self.cmd:
            self.cmd = 'list'
        if not self.name and self.options['--context=']:
            self.name = self.options['--context='][-1]
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
            et = set(ctx.get('ui_tags', []))

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
        self.print_json(config)

    async def get_contexts(self):
        with_details = ('details' in self.options['--output='])

        cfg = await self.worker.async_api_request(self.access,
            RequestConfigGet(
                contexts=(RequestConfigGet.DEEP if with_details else True)))

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
                'variable': 'ui_tags',
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
                ('ui_tags',    opts['--show-tag='])):
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
                return (msg['req_type'] == 'unlocked')
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
                    'e': friendly_date(u[0][0])  if (u and i == 0) else '',
                    'u': u[i][1]                 if (i < len(u)) else '',
                    'q': u[i][2]                 if (i < len(u)) else ''})

    def emit_json(self, config):
        self.print_json(config)

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

    Perform one-off imports of mail, or configure the named mailboxes (or
    directories) for automated importing of e-mail in the future.

    Imported mailboxes will be scanned for e-mail, adding information about
    e-mails contained within to the search engine so they can be found later
    by using `moggie search`.

    ### Options

    %(general)s

    ### Importing options

    %(import)s

    Importing messages without linking them to any particular e-mail/account
    is possible, but not recommended since moggie will be unable to ascertain
    whether a given message was "to me" (as opposed to a mailing list or
    BCC). For IMAP mailboxes, if an account is not already configured for a
    given e-mail address, it will be created.

    Without a watch-policy, this operation`will be treated as a one-off.
    If a watch or sync (for IMAP paths) policy is requested, the moggie
    configuration will be updated and `moggie new` can be used to recheck
    for mail later on.

    ### Batch operations

    To configure multiple paths, using potentialy different-but-related
    settings for each, use `--batch` and provide a list of import options
    and paths (one path per line) on standard input.

    For example, to configure a remote IMAP server in a plausible way,
    you might use a batch policy like this:

      --watch-policy=watch --account=u@example.org imap://user@host
      --tag=inbox imap://user@host/INBOX
      --tag=trash imap://user@host/Trash
      --tag=junk map://user@host/Spam

    Note the watch-policy and account settings will be inherited by the
    named mailboxes. Since the policy for the entire IMAP tree is set to
    `watch` other mailboxes may be discovered as well, but they will not
    have any tags applied. See below for more details on policy inheritance
    rules.

    If batch input starts with the character `[` it will be treated a JSON
    list of objects, instead of raw text. The above example could have been
    expressed as JSON like so:

    [
      {
        'path': 'imap://user@host',
        'watch_policy': 'watch',
        'account': 'u@example.org'
      },{
        'path': 'imap://user@host/INBOX',
        'tags': ['inbox'],
      },{
        'path': 'imap://user@host/Trash',
        'tags': ['trash'],
      },{
        'path': 'imap://user@host/Spam',
        'tags': ['junk'],
      }
    ]

    ### Policy inheritance rules

    Before performing any imports, moggie will construct an import policy
    for each path (and for watched directories, add any subdirectories to
    the plan). The policy is based on the arguments given, but the policy
    of any parent directory will be inherited by any children, unless
    expressly overridden. The special value `-` can be assigned as an
    account, tag, watch- or copy-policy to prevent such inheritance without
    specifying an alternate value.

    Note that even for one-off imports, the moggie configuration will be
    consulted and defaults may be inherited. To prevent that, be sure to
    specify `-` for any settings you do not want overridden.

    Tag inheritance is additive; that is to say if a parent has the tag
    `special`, and a mailbox specifies `inbox`, any discovered e-mail will
    in fact get both tags, unless the mailbox prevents inheritnce by
    including `-` in its list of tags.

    ### Special tags

    Moggie's importer will treat certain tags as special and enable special
    processing for mailboxes/messages that have them applied.

    The tags `inbox` and `incoming` will cause the import logic to treat any
    found messages as "new", applying user filters such as spam detection or
    mailbox sorting. After processing the `incoming` tag will always be
    removed, but whether `inbox` remains will depend on the filters.

    The `inbox` tag on a watched or sync'ed mailbox is also a signal to
    moggie that this is a mailbox to be checked relatively more frequently
    for incoming mail.

    Messages tagged as `junk` or `trash` will be excluded from search
    results by default.

    Mailboxes with `sent` or `drafts` tags will be treated as destinations
    to write moggie-generated messages to (if the watch policy is `sync`).
    """
    NAME = 'import'
    AUTO_START = True
    WEB_EXPOSE = True
    WEBSOCKET = False  # FIXME: Make this true, report progress?
    ROLES = (
        AccessConfig.GRANT_FS +
        AccessConfig.GRANT_COMPOSE +
        AccessConfig.GRANT_TAG_RW)
    OPTIONS = [[
        (None, None, 'general'),
        ('--context=', ['default'], 'X=<ctx>, import into a specific context'),
        ('--config-only',  [False], 'Update configuration only, reads no mail'),
        ('--import-only',  [False], 'One-off import, do not save configuration'),
        ('--only-inboxes',      [], ''),  # Only check mailboxes tagged with inbox
        ('--full-scan',         [], ''),  # Ignore modification times, scan everything
        ('--auto',              [], 'Guess which tags apply, based on mailbox names'),
        ('--remove=',           [], 'X=<path> Paths to remove from configuration'),
        ('--batch',             [], 'Read import options and paths from stdin'),
        ('--input=',            [], 'Read import options and paths from file'),
        ('--stdin=',            [], ''),  # Internal: lots stdin hack
        ('--compact',           [], 'Compact the search engine after importing'),
    ], [
        (None, None, 'import'),
        ('--username=',    [None], 'X=<U>, username required to access the mail (if any)'),
        ('--password=',    [None], 'X=<P>, password requried to access the mail (if any)'),
        ('--label=',       [None], 'X=label, show mailbox in the UI'),
        ('--account=',     [None], 'X=(<id>|-), the e-mail or account ID the mail belongs to'),
        ('--tag=',             [], 'X=(<tag>|-), tag to apply to all messages'),
        ('--watch-policy=',[None], 'X=(auto|watch|sync|-), set a watch policy'),
        ('--copy-policy=', [None], 'X=(copy|move|-), set the copy policy'),
    ]]

    def validate_policy(self, policy):
        for k in ('label', 'account', 'tags',
                  'watch_policy', 'copy_policy', 'tags'):
            if k not in policy:
                policy[k] = None
        if 'watch-policy' in policy:
            policy['watch_policy'] = policy['watch-policy']
            del policy['watch-policy']
        if 'copy-policy' in policy:
            policy['copy_policy'] = policy['copy-policy']
            del policy['copy-policy']
        if policy['watch_policy'] not in ('watch', 'sync', '-', None):
            raise ValueError(
                'Invalid watch policy: %s' % policy['watch_policy'])
        if policy['copy_policy'] not in ('copy', 'move', '-', None):
            raise ValueError(
                'Invalid copy policy: %s' % policy['copy_policy'])
        for tag in policy['tags'] or []:
            # FIXME: Check tag is a valid tag, what are our rules?
            if not isinstance(tag, str):
                raise ValueError('Invalid tag: %s' % tag)
        for k in policy:
            if k not in ('label', 'account', 'tags',
                         'watch_policy', 'copy_policy', 'tags'):
                raise ValueError('Invalid policy element: %s' % k)
        return policy

    def autoconf(self, path, policy):
        if not self.options['--auto']:
            return policy
        try:
            changed = True
            org_policy, policy = policy, copy.deepcopy(policy)

            path = path if isinstance(path, str) else str(path, 'utf-8')
            path = path.lower()
            basename = os.path.basename(path)
            is_imap = path.startswith('imap:')
            if is_imap:
                is_unix_spool = is_directory = False
                parts = path.split('/')
                parts.pop(0)
                if not parts[0]:
                    parts.pop(0)
                if (len(parts) == 1) or (parts[-1] == ''):
                    is_directory = True
            else:
                is_directory = os.path.exists(path) and os.path.isdir(path)
                is_unix_spool = (
                    path.startswith('/var/mail/') or
                    path.startswith('/var/spool/mail/'))

            # FIXME: Does i18n need to happen here? Do people routinely
            #        rename their systsem mailboxes?
            if is_unix_spool or 'inbox' in basename:
                policy['tags'].append('inbox')
            elif 'outbox' in basename:
                policy['tags'].append('outbox')
            elif 'sent' in basename:
                policy['tags'].append('sent')
            elif 'drafts' in basename:
                policy['tags'].append('drafts')
            elif 'junk' in basename or 'spam' in basename:
                policy['tags'].append('junk')
            else:
                changed = False

            if not policy['watch_policy']:
                changed = policy['watch_policy'] = 'watch'

            if not policy['copy_policy']:
                if is_unix_spool:
                    changed = policy['copy_policy'] = 'move'
        except UnicodeDecodeError:
            pass
        except:
            logging.exception('oops')
            raise
        finally:
            return policy if changed else org_policy

    def make_policy(self, options):
        return self.validate_policy({
            'label': options['--label='][-1],
            'account': options['--account='][-1],
            'watch_policy': options['--watch-policy='][-1],
            'copy_policy': options['--copy-policy='][-1],
            'tags': copy.copy(options['--tag='])})

    def json_configure(self, raw_json):
        try:
            if isinstance(raw_json, list):
                policies = raw_json
            else:
                import json
                policies = json.loads(raw_json.strip())
            for pol in policies:
                path = pol.pop('path')
                policy = self.validate_policy(pol)
                self.policies[path] = self.autoconf(path, policy)
                self.paths.append(path)
        except:
            logging.exception('Invalid JSON policy: %s' % raw_json)
            raise

    def batch_configure(self, lines):
        import shlex
        first = True
        for line in lines:
            if first and (isinstance(line, list) or line[:1] == '['):
                return self.json_configure('\n'.join(lines))

            line = line.strip()
            first = False
            if not line or line[:1] == '#':
                continue

            args = shlex.split(line, comments=True)
            options = copy.deepcopy(self.options)
            paths = self.strip_options(args, options)
            policy = self.make_policy(options)
            self.paths.extend(paths)
            for path in paths:
                self.policies[path] = self.autoconf(path, policy)

    def configure(self, args):
        self.newest = 0
        self.paths = []
        self.policies = {}

        self.paths = self.strip_options(args)
        self.default_policy = self.make_policy(self.options)
        self.policies = dict((p, self.autoconf(p, self.default_policy))
            for p in self.paths)

        for path in self.options['--remove=']:
            self.policies[path] = self.validate_policy({})

        if self.options['--batch'] and not self.options['--input=']:
            self.options['--input='].append('-')
        for fn in set(self.options['--input=']):
            if fn == '-':
                for stdin in self.options['--stdin=']:
                    self.batch_configure(stdin.splitlines())
                if self.stdin:
                    self.batch_configure(self.stdin)
            else:
                with open(fn, 'r') as fd:
                    self.batch_configure(fd)

        if not (self.default_policy['watch_policy']
                or self.default_policy['copy_policy']
                or self.default_policy['account']
                or self.default_policy['label']
                or self.default_policy['tags']
                or self.options['--auto']
                or self.options['--input=']):
            # If no policy is given, default to importing once with no
            # persistent configuration change.
            self.options['--import-only'] = [True]
            self.default_policy = None

        for path in self.paths:
            if path.startswith('imap:'):
                pass
            elif not os.path.exists(path):
                raise Nonsense('File or path not found: %s' % path)

        self.paths.sort()
        return []

    async def browse_paths(self, ctx_id, paths):
        yielded = set()
        done = set()
        plan = copy.copy(paths)
        while plan:
            path = plan.pop(0)
            while path.endswith('/') and len(path) > 1:
                path = path[:-1]
            if path in done:
                continue
            done.add(path)
            details = await self.repeatable_async_api_request(
                self.access,
                RequestBrowse(
                    path=path,
                    context=ctx_id,
                    username=self.options['--username='][-1],
                    password=self.options['--password='][-1]))
            for info in details['info']:
                if info['path'] not in yielded:
                    yield info['path'], info
                if info.get('is_dir'):
                    plan.append(info['path'])

    async def run_path_policies(self):
        ctx_id = self.get_context()

        # FIXME: Allow --auto with just an e-mail address? In which case
        #        we try to auto-discover the IMAP server?

        # If the user has requested --auto, then we need to first browse
        # any requested IMAP paths to generate our configurations.
        # We do not recurse filesystem paths; users that want that can run
        # find or something themselves.
        if self.options['--auto']:
            paths = [p for p in self.paths if p.startswith('imap:')]
            async for path, details in self.browse_paths(ctx_id, paths):
                if path not in self.policies:
                    policy = self.autoconf(path, self.default_policy)
                    # FIXME: Only add mailboxes here...
                    self.policies[path] = policy
                    self.paths.append(path)

        requests = []
        for path, policy in self.policies.items():
            requests.append(RequestPathPolicy(
                context=ctx_id,
                config_only=self.options['--config-only'][-1],
                import_only=self.options['--import-only'][-1],
                path=path,
                **policy))

        self.print('%s' % await self.worker.async_api_request(self.access,
            RequestPathPolicies(
                context=ctx_id,
                config_only=self.options['--config-only'][-1],
                import_only=self.options['--import-only'][-1],
                import_full=bool(self.options['--full-scan']),
                only_inboxes=bool(self.options['--only-inboxes']),
                compact=bool(self.options['--compact']),
                policies=requests)))

    async def run_import_only(self):
        ctx_id = self.get_context()
        self.print('%s' % await self.worker.async_api_request(self.access,
            RequestPathImport(
                context=ctx_id,
                import_full=bool(self.options['--full-scan']),
                only_inboxes=bool(self.options['--only-inboxes']),
                compact=bool(self.options['--compact']),
                paths=self.paths)))

    async def run(self):
        if self.default_policy is None:
            return await self.run_import_only()
        else:
            return await self.run_path_policies()

    async def UNUSED_run(self):
        def _next():
            path, request_obj = requests.pop(0)
            sys.stdout.write('[import] Processing %s\n' % path)
            self.worker.api_request(True, request_obj)

        _next()
        while True:
            try:
                if config_only:
                    msg = {
                       'message': '[import] Updated configuration only',
                       'data': {'pending': 0}}
                else:
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


class CommandNew(CommandImport):
    """# moggie new [options] [</paths/to/mailboxes/...> ...]

    Check for new mail and add to the search index.

    If no path is specified, check all configured paths that have a
    watch-policy of "watch" or "sync". If the path is not configured, this
    will have the same effect as a one-off `moggie import`.

    ### Options

    %(OPTIONS)s
    """
    NAME = 'new'
    AUTO_START = True
    WEB_EXPOSE = True
    WEBSOCKET = False  # FIXME: Make this true, report progress?
    ROLES = (
        AccessConfig.GRANT_FS +
        AccessConfig.GRANT_COMPOSE +
        AccessConfig.GRANT_TAG_RW)
    OPTIONS = [[
        (None, None, 'general'),
        ('--context=', ['default'], 'X=<ctx>, import into a specific context'),
        ('--full-scan',        [], 'Ignore modification times, scan everything'),
        ('--only-inboxes',     [], 'Only check mailboxes tagged with inbox'),
        ('--compact',          [], 'Compact the search engine after importing'),
    ]]

    def configure(self, args):
        self.paths = []
        self.paths = self.strip_options(args)
        self.default_policy = None
        return []

    async def run(self):
        return await self.run_import_only()


class CommandBrowse(CLICommand):
    """# moggie browse [options] </path/>

    Search the given path for folders and mailboxes.

    ### Options

    %(OPTIONS)s

    Note that the command runs on the moggie back-end, so may be exploring
    a different filesystem from the one running the moggie CLI.
    """
    NAME = 'browse'
    ROLES = (
        AccessConfig.GRANT_FS +
        AccessConfig.GRANT_NETWORK)
    WEBSOCKET = False
    WEB_EXPOSE = True
    AUTO_START = False
    IGNORED = ['.', '..',
        'cur', 'new', 'tmp', 'wervd.ver',
        '.git', '.notmuch', '.cache', '.dbus', '.fossil', '.muttrc',
        '.procmailrc', '.subversion', '.Xauthority',
        '.ssh', '.gnupg', 'secring.gpg', 'private-keys-v1.d',
        '.fetchmailrc', '.password-store', 'etc', 'passwd']
    OPTIONS = [[
        ('--format=',     ['text'], 'X=(text*|json|sexp)'),
        ('--tabs',         [False], 'Use tabs to separate output columns'),
        ('--context=', ['default'], 'X=<ctx>, import messages into a specific context'),
        ('--username=',     [None], 'X=<U>, username required to access the data (if any)'),
        ('--password=',     [None], 'X=<P>, password requried to access the data (if any)'),
        ('--ifnewer=',         [0], 'X=<ts>, ignore files unchanged since timestamp'),
        ('--ignore=',      IGNORED, '')]]

    SRC_ORDER = {
        'config': 0,
        'spool': 1,
        'home': 2,
        'fs': 10,
        'mailpilev1': 20,
        'thunderbird': 30}

    SRC_DESCRIPTIONS = {
        '': 'Files and folders',
        'fs': 'Files and folders',
        'imap': 'Remote IMAP mailboxes',
        'spool': 'Incoming system mail',
        'config': 'Moggie settings',
        'home': 'Your home directory',
        'mailpilev1': 'Legacy Mailpile v1 data',
        'thunderbird': 'Thunderbird mailboxes'}

    def configure(self, args):
        self.paths = self.strip_options(args)
        for p in self.paths:
            while p.endswith('/') and len(p) > 1:
                p = p[:-1]
            if os.path.basename(p) in self.IGNORED:
                raise Nonsense('Invalid path: %s' % p)
        if not self.paths:
            self.paths = [True]  # Request the default browse list
        return []

    def emit_text(self, path, results):
        if not isinstance(results, list):
            self.print('%s' % results)
            return

        tabs = self.options['--tabs'][-1]
        if tabs:
            fmt = '%(tag)s\t%(path)s\t%(size)s\t%(mtime)s\t%(magic)s'
        else:
            width = 15
            for child in results:
                width = max(len(child['path']), width)
            fmt = (
                '  %%(path)-%ds %%(size)10s %%(mtime)10s %%(magic)s') % width

        first = True
        explanations = copy.copy(self.SRC_DESCRIPTIONS)
        for child in results:
            if not isinstance(child, dict):
                self.print('%s' % child)
                continue

            magic = child.get('magic', [])
            if child.get('is_dir'):
                magic.append('dir')
            for attr in ('size', 'mtime'):
                if attr not in child:
                    child[attr] = '' if tabs else None
            child['src'] = child.get('src', 'fs')
            child['magic'] = ','.join(sorted(magic))
            if not tabs:
                child['mtime'] = friendly_datetime(child['mtime'])
                child['size'] = friendly_bytes(child['size'])

            if child['src'] in explanations and not tabs:
                if not first:
                    self.print()
                self.print(explanations[child['src']] + ':')
                del explanations[child['src']]
                first = False
            self.print(fmt % child)
        if not tabs:
            self.print()

    async def run(self):
        ctx_id = self.get_context()
        acct_id = mailbox_label = mailbox_tags = mailbox_policy = None

        ignored = set(self.options['--ignore='])
        def _prune(results):
            if 'info' in results:
                results = [r for r in results['info']
                    if r['path'][:5] in ('imap:', b'imap:')
                    or (r.get('exists') and
                        (r.get('magic') or r.get('has_children')) and
                        os.path.basename(r['path']) not in ignored)]
                for r in results:
                    for d in ('mode', 'owner', 'group', 'exists'):
                        if d in r:
                            del r[d]
                    if 'is_dir' in r and not r['is_dir']:
                        del r['is_dir']
                    if 'bytes' in r.get('magic', []):
                        r['magic'].remove('bytes')
            return results

        fmt = self.options['--format='][-1]
        results = {}
        for path in self.paths:
            if path is not True:
                while path.endswith('/') and len(path) > 1:
                    path = path[:-1]
            results[path] = _prune(
                await self.repeatable_async_api_request(self.access,
                    RequestBrowse(
                        context=ctx_id,
                        path=path,
                        ifnewer=int(self.options['--ifnewer='][-1]),
                        username=self.options['--username='][-1],
                        password=self.options['--password='][-1])))

            results[path].sort(key=self.sort_key)
            if fmt == 'text':
                self.emit_text(path, results[path])

        if fmt == 'json':
            self.print_json(results)
        elif fmt == 'sexp':
            self.print_sexp(results)

    def sort_key(self, result):
        return (
            self.SRC_ORDER.get(result.get('src', 'fs'), 999),
            '/.' in result['path'],
            result['path'].lower(),
            result.get('mtime', 0))


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
            if '.' in section:
                section, opt = section.rsplit('.', 1)
                options = [opt]
            else:
                options = cfg[section].keys()
        print('[%s]' % (section,))
        for opt in options:
            try:
                print('%s = %s' % (opt, cfg[section][opt]))
            except ValueError:
                print('%s = (encrypted)' % (opt,))
            except KeyError:
                print('# %s = (unset)' % (opt,))

    elif args[0] == 'set':
        try:
            section, option, value = args[1:4]
            cfg.set(section, option, value, save=True)
            print('[%s]\n%s = %s' % (section, option, cfg[section][option]))
        except KeyError:
            print('# Not set: %s / %s' % (section, option))
