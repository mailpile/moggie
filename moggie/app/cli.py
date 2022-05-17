import os
import sys

# TODO: add a status command, to check what is live?

class Nonsense(Exception):
    pass


def CommandImport(wd, args):
    from ..config import AppConfig
    from ..workers.app import AppWorker
    from ..jmap.requests import RequestMailbox, RequestSearch, RequestAddToIndex

    SEARCH = ('in:incoming',)

    paths = []
    while args and ((args[-1] in SEARCH) or os.path.exists(args[-1])):
        paths.append(os.path.abspath(args.pop(-1)))
    paths.reverse()
    if not paths:
        raise Nonsense('No valid paths found!')

    worker = AppWorker.FromArgs(wd, args[0:])
    if not worker.connect():
        raise Nonsense('Failed to connect to app')


    for path in paths:
        print('Adding %s' % path)
        if path in SEARCH:
            request_obj = RequestSearch(
                context=AppConfig.CONTEXT_ZERO,
                terms=path)
        else:
            request_obj = RequestMailbox(
                context=AppConfig.CONTEXT_ZERO,
                mailbox=path)
        worker.jmap(RequestAddToIndex(
            context=AppConfig.CONTEXT_ZERO,
            search=request_obj,
            force=(path in SEARCH)))


def CommandHelp(wd, args):
    from . import helps
    return helps.HelpCLI(wd, args)


def CommandEnableEncryption(wd, args):
    from ..config import AppConfig
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

        cfg.provide_passphrase(p1, contacts=ct)
        if cfg.has_crypto_enabled:
            print('Great, that passsphrase works!')
        else:
            cfg.generate_master_key()
            print('Encryption enabled, good job!')

    except PermissionError as e:
        print('# oops: %s' % e)


def CommandConfig(wd, args):
    from ..config import AppConfig
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


CLI_COMMANDS = {
    'help': CommandHelp,
    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'config': CommandConfig}
