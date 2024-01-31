import logging
import os
import sys
import time


COMMANDS = {}


class Nonsense(Exception):
    pass


def CommandStart(moggie, args):
    wait = '--wait' in args
    if wait:
        args.remove('--wait')

    # FIXME: Get from the moggie object
    from ..workers.app import AppWorker
    worker = AppWorker.FromArgs(moggie.work_dir, args)

    if worker.connect():
        if wait or ('--wait' in args):
            worker.join()
        else:
            moggie._tell_user('Running %s in the background.' % worker.KIND)
        return worker
    else:
        return False


def CommandStop(moggie, args, exit=True):
    from ..workers.app import AppWorker
    worker = AppWorker.FromArgs(moggie.work_dir, args[0:])

    if worker.connect(autostart=False):
        result = worker.quit()
        if result and result.get('quitting'):
            moggie._tell_user('Shutting down %s.' % worker.KIND)
            return True
        else:
            return False
    moggie._tell_user('Not running? (%s)' % worker.KIND)
    return True


def CommandRestart(moggie, args):
    try:
        if not CommandStop(moggie, []):
            return False
        time.sleep(1)
    except:
        pass
    return CommandStart(moggie, args)


def CommandTUI(moggie, tui_args, draft=[]):
    from . import tui
    return tui.Main(moggie, tui_args, draft)


def CommandMuttalike(moggie, args):
    """
    This command will be a shim which implements many of the same
    command line options as mutt, in a moggie way.
    """
    from ..email.draft import MessageDraft

    single = (
        '-d', '-D', '-E', '-R', '-h', '-n',
        '-p', '-v' '-vv' , '-y', '-z', '-Z')

    sys_args = {'_order': [], '--': []}
    tui_args = {'_order': [], '--': []}
    draft = None
    passing = []

    def _eat(target, a0, args):
        if a0 in target['_order']:
            raise Nonsense('Duplicate argument: %s' % a0)
        target['_order'].append(a0)
        if a0 in single:
            target.update({a0: True})
        else:
            eating = args.pop(0)
            target[a0] = eating

    class Done(Exception):
        def __init__(self, result):
            self.result = result
            super().__init__()

    def _process(arg, args):
        if arg == '-h':
            raise Done(moggie.help(*args))
        elif arg in ('-E', '-f', '-p', '-R', '-y', '-Z'):
            _eat(tui_args, arg, args)
        elif arg in ('-d', '-D', '-F', '-m', '-n'):
            _eat(sys_args, arg, args)
        else:
            raise Nonsense('Invalid argument: %s' % arg)

    try:
        draft = MessageDraft.FromArgs(args, unhandled_cb=_process)

        tui_exclusive = ('-f', '-p', '-Z')
        for a in (arg for arg in tui_exclusive if arg in tui_args):
            for b in tui_exclusive:
                if a != b and (b in tui_args):
                    raise Nonsense('Cannot %s and %s at once' % (a, b))

        if not tui_args['_order'] and not tui_args['--']:
            tui_args = {}

        if not sys_args['_order'] and not sys_args['--']:
            sys_args = {}

    except Done as d:
        return d.result

    except Nonsense as e:
        raise

    except Exception as e:
        logging.exception(e)
        raise

    if '-d' in sys_args:
        from ..config import configure_logging
        loglevel = max(0, min(int(sys_args['-d']), 4))
        loglevel = [
            logging.CRITICAL,
            logging.ERROR,
            logging.WARNING,
            logging.INFO,
            logging.DEBUG
            ][loglevel]
        logfile = configure_logging(
            stdout=False,
            profile_dir=moggie.work_dir,
            level=loglevel)
        if loglevel <= logging.INFO:
            moggie._tell_user('Logging to %s (startup in 2s)' % (logfile,))
            time.sleep(2)

    if sys.stdin.isatty() and sys.stdout.isatty():
        return CommandTUI(moggie, tui_args, draft)

    elif draft:
        draft.more['message'] = [sys.stdin.read()]
        draft_as_args = draft.email_args()
        # FIXME: mutt will send the message automatically!
        #        To be compatible, we should add --send-at=NOW
        return moggie.email(*draft_as_args)

    return moggie.help()


def Main(args):
    from moggie import get_shared_moggie
    from .cli.exceptions import NotRunning

    moggie = get_shared_moggie(name='cli')
    moggie.set_mode(moggie.MODE_CLI)
    moggie.enable_default_logging()

    try:
        command = 'default'
        if len(args) > 0 and args[0][:1] != '-' and '@' not in args[0]:
            command = args.pop(0)

        if moggie.run(command, *args) != [False]:
            if command in ('start', 'restart'):
                # We need to use os._exit(0) here, to avoid hanging when
                # the user has started a new Moggie background process.
                os._exit(0)
            else:
                sys.exit(0)

    except NotRunning as e:
        moggie._tell_user('Error: %s' % e)

    except Nonsense as e:
        moggie._tell_user('Error: %s' % e)

    sys.exit(1)
