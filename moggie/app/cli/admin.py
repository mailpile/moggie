# TODO: add a status command, to check what is live?

import os

from .command import Nonsense, CLICommand


class CommandUnlock(CLICommand):
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

        from ...jmap.requests import RequestUnlock
        self.app.send_json(RequestUnlock(self.get_passphrase()))
        msg = await self.await_messages('unlocked', 'notification')
        if msg and msg.get('message'):
            print(msg['message'])
            return (msg['prototype'] == 'unlocked')
        else:
            print('Unknown error (%s) or timed out.' % msg)
            return False


class CommandImport(CLICommand):
    SEARCH = ('in:incoming',)

    def configure(self, args):
        self.paths = []
        while args and ((args[-1] in self.SEARCH) or os.path.exists(args[-1])):
            self.paths.append(os.path.abspath(args.pop(-1)))
        self.paths.reverse()
        if not self.paths:
            raise Nonsense('No valid paths found!')
        return args

    async def run(self):
        from ...config import AppConfig
        from ...jmap.requests import RequestMailbox, RequestSearch, RequestAddToIndex

        for path in self.paths:
            print('Adding %s' % path)
            if path in self.SEARCH:
                request_obj = RequestSearch(
                    context=AppConfig.CONTEXT_ZERO,
                    terms=path)
            else:
                request_obj = RequestMailbox(
                    context=AppConfig.CONTEXT_ZERO,
                    mailbox=path)
            self.worker.jmap(RequestAddToIndex(
                context=AppConfig.CONTEXT_ZERO,
                search=request_obj,
                force=(path in self.SEARCH)))


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
