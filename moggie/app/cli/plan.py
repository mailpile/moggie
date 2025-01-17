import asyncio
import copy
import json
import logging
import os
import shlex
import sys
import time
import traceback

from ...api.exceptions import *
from ...api.requests import *
from ...email.addresses import AddressInfo, AddressHeaderParser
from ...email.headers import ADDRESS_HEADERS
from ...email.metadata import Metadata
from ...util.dumbcode import to_json, from_json
from ...util.friendly import *
from .command import Nonsense, CLICommand


class CommandPlan(CLICommand):
    """# moggie plan [options] <scenario|command [command2 ...]>

    Generate one or more sets of commands and options (ordered by
    preference).

    The options reflect the default settings, and may be further refined
    or reordered depending on the scenario and by passing options such
    as `--emailing=` to indicate what the user is doing.

    ### Commands and scenarios

    `moggie plan` will advise on how to run the following commands:

      copy     Composition: where to store generated mail
      email    Composition: e-mail generation defaults
      send     E-mail sender and outgoing mail server settings

    The following scenarios will generate plans involving multiple
    commands. To use a scenario with `--format=xargs`, note you must
    also choose which command to generate for (e.g. `compose email`).

      compose  Compose a new e-mail
      forward  Forward an existing e-mail
      reply    Reply to an e-mail (reply-all)
      reply1   Reply to an e-mail (selected recipient/s)
      retry    Retry sending one or more e-mails (requires --message=)

    ### Options

    %(OPTIONS)s

    ### Feeling Lucky

    The first-choice `moggie plan` option set can be fed directly to the
    desired command, by using the `xargs` tool like so:

        $ moggie plan email --xargs |xargs -0 moggie email ...

    Or to use a specific scenario:

        $ moggie plan reply1 email --xargs |xargs -0 ...

    Note the use of the `-0` argument to `xargs` to guarantee that data
    which is split over multiple lines (e.g. e-mail signatures) gets
    passed correctly.
    """
    NAME = 'plan'
    WEBSOCKET = False
    WEB_EXPOSE = True
    AUTO_START = False
    OPTIONS = [[
        ('--context=', ['default'], 'X=<ctx>, context we need config for'),
        ('--format=',     ['bash'], 'X=(bash*|json|sexp|xargs)'),
        ('--tabs',         [False], 'Use tabs to separate output columns'),
        ('--message=',          [], 'X=<search terms>, e-mail(s) for context'),
        ('--emailing=',         [], 'X=<address>, config for sending emails'),
        ('--xargs',        [False], 'Shorthand for --format=xargs'),
        ]]
    SCENARIOS = {
        'compose': ['email', 'copy', 'send'],
        'forward': ['email', 'copy', 'send'],
        'reply':   ['email', 'copy', 'send'],
        'reply1':  ['email', 'copy', 'send'],
        'retry':   ['send']}

    def __init__(self, *args, **kwargs):
        self.transforms = {
            'compose/copy': self.transform_compose_copy,
            'compose/email': self.transform_email,
            'compose/send': self.transform_send,
            'forward/copy': self.transform_compose_copy,
            'forward/email': self.transform_email,
            'forward/send': self.transform_send,
            'reply/copy': self.transform_compose_copy,
            'reply/email': self.transform_email,
            'reply/send': self.transform_send,
            'reply1/copy': self.transform_compose_copy,
            'reply1/email': self.transform_email,
            'reply1/send': self.transform_send,
            'retry/send': self.transform_retry_send}
        super().__init__(*args, **kwargs)

    def configure(self, args):
        self.cmds = self.strip_options(args)
        self.scenario = 'compose'

        if self.cmds and self.cmds[0] in self.SCENARIOS:
            self.scenario = self.cmds.pop(0)
            if not self.cmds:
                self.cmds = self.SCENARIOS[self.scenario]

        if self.options['--xargs'][-1]:
            self.options['--format='].append('xargs')

        fmt = self.options['--format='][-1]
        if fmt == 'text':
            self.options['--format='][-1] = fmt = 'bash'

        if fmt not in ('xargs', 'bash', 'json', 'sexp'):
            raise Nonsense('Unsupported output format')

        if self.scenario in ('reply', 'reply1', 'forward'):
            if not self.options['--message=']:
                raise Nonsense('Replying and forwarding require --message=')

        # Convert --message= metadata or error out if invalid
        for i, msg in enumerate(self.options['--message=']):
            if msg[:1] == '{':
                self._parsed_message(i, json.loads(msg))
            elif msg[:2] == '[[':
                self._parsed_message(i, Metadata(*json.loads(msg)).parsed())

        self.context_emails = context_emails = set()
        self.emailing = []
        for e in self.options['--emailing=']:
            for ai in AddressHeaderParser(e):
                context_emails.add(ai.address)
                self.emailing.append(ai)
        for msg in self.options['--message=']:
            if isinstance(msg, dict):
                self._gather_context_emails(msg)

        return []

    def _gather_context_emails(self, msg):
        for hdr in ADDRESS_HEADERS:
            val = msg.get(hdr) or []
            for ai in (val if isinstance(val, list) else [val]):
                if 'address' in ai:
                    self.context_emails.add(ai['address'])

    async def get_context_config(self):
        res = await self.worker.async_api_request(self.access,
            RequestConfigGet(contexts=RequestConfigGet.DEEP))
        return res['config'].get('contexts', {})

    def _parsed_message(self, i, msgp):
        if 'from' not in msgp or 'date' not in msgp:
            raise Nonsense('Failed to parse %s as message' % msgp)
        if i is not None:
            self.options['--message='][i] = msgp
        else:
            self.options['--message='].append(msgp)

    async def gather_messages(self):
        # FIXME: Handle terms within mailboxes, see moggie parse for pattern
        for msg_idx, msg in enumerate(copy.copy(self.options['--message='])):
            results = []
            async for res in super().gather_emails([([], msg)]):
                if 'email' in res:
                    if 'metadata' in res:
                        res['email']['_METADATA'] = Metadata(*res['metadata']).parsed()
                    results.append(res['email'])

            for i, parsed in enumerate(results):
                if i == 0:
                    self._parsed_message(msg_idx, parsed)
                else:
                    self._parsed_message(None, parsed)

                self._gather_context_emails(parsed)

    def _get_account_id(self, config, email):
        for acct, info in config['accounts'].items():
            if email in info['addresses']:
                return acct
        raise KeyError(email)

    def _get_account(self, config, email):
        for acct, info in config['accounts'].items():
            if email in info['addresses']:
                return info

        # Not found: Is it a plussed address? Try again?
        if '+' in email.split('@', 1)[0]:
            userpart = email.split('@', 1)[0].split('+')[0]
            email = '%s@%s' % (userpart, email.split('@', 1)[1])
            return self._get_account(config, email)

        return {'addresses': [email]}

    def _get_identities(self, config):
        return config['identities'].items()

    async def transform_compose_copy(self, config):
        _ga = lambda email: self._get_account(config, email)
        return dict((_id, {
                'context': [self.context],
                'tag': ['+drafts', '+_mp_incoming_old'],
                'target': [_ga(identity['address']).get('write_to_mailbox')],
            }) for _id, identity in self._get_identities(config))

    def _add(self, args, var, val):
        val = val.strip() if isinstance(val, str) else val
        if var in args:
            args[var].append(val)
        else:
            args[var] = [val]

    async def transform_email(self, config):
        arg_sets = dict((_id, {
                # FIXME: Add crypto-related keys!
                'context': [self.context],
                'from': [('%(name)s <%(address)s>' % identity).strip()],
                'signature': [identity.get('signature', None)]
            }) for _id, identity in self._get_identities(config))

        if self.scenario in ('reply', 'reply1'):
            for _id, args in arg_sets.items():
                seen = set([args['from'][0].split('<', 1)[1].rstrip('>')])
                if (self.scenario == 'reply1') and self.emailing:
                    for r in self.emailing:
                        self._add(args, 'to', '%(fn)s <%(address)s>' % r)

                references = []
                in_reply_tos = []
                for msg in self.options['--message=']:
                    if 'subject' not in args:
                        args['subject'] = ['Re: ' + msg['subject']]

                    message_id = msg.get('message-id')
                    if message_id:
                        in_reply_tos.append(message_id)

                    old_refs = msg.get('references') or []
                    for ref in (r.strip() for r in old_refs):
                        if ref not in references:
                            references.append(ref)

                    if (self.scenario == 'reply1') and self.emailing:
                        continue

                    for reply_to in ('reply-to', 'from', 'sender'):
                        rt = msg.get(reply_to)
                        if rt:
                            self._add(args, 'to', '%(fn)s <%(address)s>' % rt)
                            seen.add(rt['address'])
                            break

                    if self.scenario == 'reply1':
                        continue

                    for hdr in ('to', 'cc', 'bcc'):
                        hval = msg.get(hdr) or []
                        hval = hval if isinstance(hval, list) else [hval]
                        for rcpt in hval:
                            if rcpt['address'] not in seen:
                                seen.add(rcpt['address'])
                                rcpt = '%(fn)s <%(address)s>' % rcpt
                                self._add(args, 'cc', rcpt)

                if in_reply_tos or references:
                    references.extend(in_reply_tos)
                    if in_reply_tos:
                        self._add(args, 'header', 'In-Reply-To:%s' % in_reply_tos[0])
                    self._add(args, 'header', 'References:%s' % ' '.join(references))
        else:
            for _id, args in arg_sets.items():
                for r in self.emailing:
                    self._add(args, 'to', '%(fn)s <%(address)s>' % r)

        return arg_sets

    def _common_send_args(self):
        return {
            'context': [self.context],
            'tag-sending': ['-drafts', '+outbox', '+_mp_incoming_old'],
            'tag-failed': ['+inbox', '+urgent'],
            'tag-sent': ['-outbox', '+sent'],
            'send-to': []}

    async def transform_retry_send(self, config):
        send_config = {}
        for i, msg in enumerate(self.options['--message=']):
            if isinstance(msg, dict):
                _id = msg.get('_METADATA', {}).get('idx', '#%s' % i)
                send_config[_id] = send_cfg = self._common_send_args()
                send_cfg['ARGS'] = ['--retry-now', 'id:%s' % _id]

        return send_config

    async def transform_send(self, config):
        _ga_id = lambda email: self._get_account_id(config, email)

        send_config = {}
        email_config = await self.transform_email(config)
        for _id, identity in self._get_identities(config):
            send_config[_id] = send_cfg = self._common_send_args()

            email_cfg = email_config.get(_id, {})
            for hdr in ('to', 'cc', 'bcc'):
                for rcpt in email_cfg.get(hdr, []):
                    for ai in AddressHeaderParser(rcpt):
                        send_cfg['send-to'].append(ai.address)

            if send_cfg.get('send-to'):
                send_cfg.update({
                    'send-from': [identity['address']],
                    'send-at': [config.get('default_send_at', '+120')],
                    'send-via': ['@%s' % _ga_id(identity['address'])]})

        return send_config

    async def transform(self, cmd, config):
        return await (self.transforms[self.scenario+'/'+cmd])(config)

    def print_xargs(self, results):
        _id, cmd_opts = results[0]
        opts = cmd_opts[self.cmds[0]]
        args = []
        for k, vlist in opts.items():
            args.extend((k, v) for v in vlist)
        self.print('\0\n'.join('--%s=%s' % (k, v) for k, v in args))

    def print_bash(self, results):
        def sh_name(name):
            return name.lstrip('-').rstrip('=').replace('-', '_')

        opts = ['OPT%d' % (c+1) for c in range(len(results))]
        bash = [
            'MOGGIE_CONTEXT=%s' % shlex.quote(self.context),
            'MOGGIE_SCENARIO=%s' % shlex.quote(self.scenario),
            'MOGGIE_COMMANDS=( %s )' % ' '.join(self.cmds),
            'MOGGIE_OPT_SETS=( %s )' % ' '.join(opts),
            '']

        for idx, (_id, cmd_opts) in enumerate(results):
            bash.append('# %s' % _id)
            for cmd in self.cmds:
                for k, vlist in cmd_opts[cmd].items():
                    k = 'OPT%d_%s__%s' % (idx+1, sh_name(cmd), sh_name(k))
                    for i, v in enumerate(vlist):
                        op = '+=' if i else '='
                        bash.append(
                            'MOGGIE_%s%s( %s )' % (k, op, shlex.quote(v)))
            bash.append('')

        self.print('\n'.join(bash))

    async def sort_composer_results(self, results):
        rcpts = self.context_emails
        if rcpts:
            rcpt_doms = set(r.split('@')[-1] for r in rcpts)
            def _rank(r):
                args = r[1].get('send') or r[1].get('email')
                address = (args.get('send-from') or args.get('from') or [''])[0]
                if '<' in address:
                    address = address.split('<', 1)[1].rstrip('>')

                domain = address.split('@', 1)[-1]
                return (address not in rcpts, domain not in rcpt_doms)
            results.sort(key=_rank)
        return results

    async def sort_and_filter(self, results):
        # FIXME: Pluggable mechanism for sorting/filtering/tweaking identities
        # FIXME: Extend for non-composer related planning
        return await self.sort_composer_results(results)

    async def run(self):
        fmt = self.options['--format='][-1]
        if fmt == 'xargs' and len(self.cmds) != 1:
            raise Nonsense('--format=xargs only supports one command at a time')

        ctx_id = self.get_context()
        config = (await self.get_context_config()).get(ctx_id, {})

        await self.gather_messages()

        results = {}
        for cmd in self.cmds:
            for _id, opts in (await self.transform(cmd, config)).items():
                d = results.get(_id, {})
                d[cmd] = opts
                results[_id] = d

        results = await self.sort_and_filter(list(results.items()))

        if fmt == 'json':
            self.print_json(results)
        elif fmt == 'sexp':
            self.print_sexp(results)
        elif fmt in 'xargs':
            self.print_xargs(results)
        elif fmt in ('bash', 'text'):
            self.print_bash(results)

