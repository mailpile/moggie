import logging
import os
import sys
import time


COMMANDS = {}
DEFAULT_LOG_LEVEL = 2


class Nonsense(Exception):
    pass


def CommandStart(wd, args):
    wait = '--wait' in args
    if wait:
        args.remove('--wait')

    from ..workers.app import AppWorker
    worker = AppWorker.FromArgs(wd, args)

    if worker.connect():
        if wait or ('--wait' in args):
            worker.join()
        else:
            sys.stderr.write('Running %s in the background.\n' % worker.KIND)
        os._exit(0)
    else:
        sys.exit(1)


def CommandStop(wd, args, exit=True):
    from ..workers.app import AppWorker
    worker = AppWorker.FromArgs(wd, args[0:])

    if worker.connect(autostart=False):
        result = worker.quit()
        if result and result.get('quitting'):
            sys.stderr.write('Shutting down %s.\n' % worker.KIND)
            if exit:
                sys.exit(0)
            else:
                return
    sys.stderr.write('Not running? (%s)\n' % worker.KIND)
    sys.exit(1)


def CommandRestart(wd, args):
    try:
        CommandStop(wd, args, exit=False)
        time.sleep(1)
    except:
        pass
    CommandStart(wd, args)


def CommandTUI(wd, tui_args, draft=[]):
    from . import tui
    return tui.Main(wd, tui_args, draft)


def CommandMuttalike(wd, args):
    """
    This command will be a shim which implements many of the same
    command line options as mutt, in a moggie way.
    """
    from ..email.draft import MessageDraft

    single = ('-D', '-E', '-R', '-h', '-n', '-p', '-v' '-vv' , '-y', '-z', '-Z')
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

    def _process(arg, args):
        if arg == '-h':
            return COMMANDS.get('help').Command(wd, args)
        elif arg in ('-E', '-f', '-p', '-R', '-y', '-Z'):
            _eat(tui_args, arg, args)
        elif arg in ('-d', '-D', '-F', '-m', '-n'):
            _eat(sys_args, arg, args)
        else:
            raise Nonsense('Invalid argument: %s' % arg)

    try:
        draft = MessageDraft.FromArgs(args, unhandled_cb=_process)

        tui_exclusive = ('-f', '-p', '-y', '-Z')
        for a in (arg for arg in tui_exclusive if arg in tui_args):
            for b in tui_exclusive:
                if a != b and (b in tui_args):
                    raise Nonsense('Cannot %s and %s at once' % (a, b))

        if not tui_args['_order'] and not tui_args['--']:
            tui_args = {}

        if not sys_args['_order'] and not sys_args['--']:
            sys_args = {}

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
            profile_dir=wd,
            level=loglevel)
        if loglevel <= logging.INFO:
            sys.stderr.write('Logging to %s (startup in 2s)\n' % (logfile,))
            time.sleep(2)

    if sys.stdin.isatty() and sys.stdout.isatty():
        return CommandTUI(wd, tui_args, draft)

    elif draft:
        draft.more['message'] = [sys.stdin.read()]
        draft_as_args = draft.email_args()
        # FIXME: mutt will send the message automatically!
        #        To be compatible, we should add --send-at=NOW
        return COMMANDS.get('email').Command(wd, draft_as_args)

    return COMMANDS.get('help').Command(wd, [])


def Main(args):
    from ..config.paths import DEFAULT_WORKDIR
    from ..config import configure_logging, AppConfig
    wd = DEFAULT_WORKDIR()

    command = 'default'
    if len(args) > 0 and args[0][:1] != '-' and '@' not in args[0]:
        command = args.pop(0)

    # Merge our local commands with the CLI_COMMANDS registry.
    # Trust me, I know what I'm doing!
    global COMMANDS
    from .cli import CLI_COMMANDS
    CLI_COMMANDS.update({
        'default': CommandMuttalike,
        'restart': CommandRestart,
        'start': CommandStart,
        'stop': CommandStop,
        'tui': CommandTUI})
    COMMANDS = CLI_COMMANDS

    global DEFAULT_LOG_LEVEL
    DEFAULT_LOG_LEVEL = int(AppConfig(wd).get(
        AppConfig.GENERAL, 'log_level', fallback=logging.DEBUG))
    configure_logging(profile_dir=wd, level=DEFAULT_LOG_LEVEL)

    def _run(command):
        if hasattr(command, 'Command'):
            result = command.Command(wd, args)
        else:
            result = command(wd, args)
        if result is False:
            sys.exit(1)

    command = COMMANDS.get(command)
    if command is not None:
        try:
            _run(command)
            sys.exit(0)
        except Nonsense as e:
            sys.stderr.write('Error: %s\n' % e)
        sys.exit(1)
    else:
        _run(COMMANDS.get('help'))
