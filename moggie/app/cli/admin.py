# TODO: add a status command, to check what is live?

import asyncio
import logging
import os
import sys

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
        while True:
            msg = await self.await_messages('unlocked', 'notification')
            if msg and msg.get('message'):
                print(msg['message'])
                return (msg['prototype'] == 'unlocked')
            else:
                print('Unknown error (%s) or timed out.' % msg)
                return False


class CommandImport(CLICommand):
    """# moggie import [options] </path/to/mailbox1> [</path/to/mbx2> [...]]

    Scan the named mailboxes for e-mail, adding any found messages to the
    search engine. Re-importing a mailbox will check for updates/changes to
    the contents.

    Options:
      --context=ctx  Specify the context for the imported messages
      --ifnewer=ts   Ignore folders and files unchanged since the timestamp
      --recurse      Search the named paths recursively for mailboxes
      --compact      Compact the search engine after importing
      --watch        Add these to our list of locations to watch for mail
      --old          Treat messages as "old": do not add to inbox etc.

    """
    SEARCH = ('in:incoming',)
    OPTIONS = {
        '--context=':  ['default'],
        '--ifnewer=':  [],
        '--ignore=':   ['.', '..', 'cur', 'new', 'tmp', '.notmuch'],
        '--recurse':   [],
        '--compact':   [],
        '--watch':     [],
        '--dryrun':    [],
        '--old':       []}

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
        from ...jmap.requests import RequestMailbox, RequestSearch, RequestAddToIndex

        requests = []
        for path in self.paths:
            if path in self.SEARCH:
                request_obj = RequestSearch(
                    context=AppConfig.CONTEXT_ZERO,
                    terms=path)
            else:
                request_obj = RequestMailbox(
                    context=AppConfig.CONTEXT_ZERO,
                    mailbox=path)

            requests.append((path, RequestAddToIndex(
                context=AppConfig.CONTEXT_ZERO,
                search=request_obj,
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
            self.worker.jmap(request_obj)

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
