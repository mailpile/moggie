# These are CLI commands which aim to behave as similarly to notmuch as
# possible. Because why not? Compatibility is nice.

from .command import CLICommand


class CommandSearch(CLICommand):
    def configure(self, args):
        self.terms = ' '.join(args)
        return []

    def emit_result(self, md):
        mdp = md.parsed()
        print('%5.5s %s' % (md.idx, mdp))

    async def run(self):
        from ...config import AppConfig
        from ...jmap.requests import RequestSearch
        from ...email.metadata import Metadata

        query = RequestSearch(
            context=AppConfig.CONTEXT_ZERO,
            terms=self.terms)
        self.app.send_json(query)

        # FIXME: Perform partial searches and iterate through

        while True:
            msg = await self.await_messages('search', 'notification')
            if msg.get('req_id') == query['req_id']:
                for md in (Metadata(*e) for e in reversed(msg['emails'])):
                    self.emit_result(md)
            if msg and msg.get('message'):
                print(msg['message'])
                return (msg['prototype'] == 'unlocked')
            else:
                return False


class CommandAddress(CommandSearch):
    def emit_result(self, md):
        print('%s' % md.parsed()['from'])


class CommandCount(CLICommand):
    pass


class CommandTag(CLICommand):
    pass




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
