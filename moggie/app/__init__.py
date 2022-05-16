import logging
import os
import sys


class Nonsense(Exception):
    pass


def CommandStart(wd, args):
    wait = 'wait' in args
    if wait:
        args.remove('wait')

    from ..workers.app import AppWorker
    worker = AppWorker.FromArgs(wd, args[0:])

    if worker.connect():
        if wait:
            worker.join()
        else:
            sys.stderr.write('Running %s in the background.\n' % worker.KIND)
        os._exit(0)
    else:
        sys.exit(1)


def CommandStop(wd, args):
    from ..workers.app import AppWorker
    worker = AppWorker.FromArgs(wd, args[0:])

    if worker.connect(autostart=False):
        result = worker.quit()
        if result and result.get('quitting'):
            sys.stderr.write('Shutting down %s.\n' % worker.KIND)
            sys.exit(0)
    sys.stderr.write('Not running? (%s)\n' % worker.KIND)
    sys.exit(1)


def CommandTUI(wd, sys_args, tui_args, send_args):
    from . import tui
    return tui.Main(wd, sys_args, tui_args, send_args)


def CommandMuttalike(wd, args):
    """
    This command will be a shim which implements many of the same
    command line options as mutt, in a moggie way.
    """
    from ..email.draft import FakeDraftMain, MessageDraft
    single = ('-D', '-E', '-R', '-h', '-n', '-p', '-v' '-vv' , '-y', '-z', '-Z')
    def _eat(target):
        a0 = args[0]
        if a0 in target['_order']:
            raise Nonsense('Duplicate argument: %s' % a0)
        target['_order'].append(a0)

        if a0 in single:
            target.update({args.pop(0): True})
        else:
            eating = args[:2]
            args[:2] = []
            target[eating[0]] = eating[1]

    sys_args = {'_order': [], '--': []}
    tui_args = {'_order': []}
    send_args = {'_order': []}
    try:
        while args:
            if args[0] == '-h':
                return COMMANDS['help'](wd, args[1:])
            elif args[0] in MessageDraft.ALL_CLI_ARGS:
                _eat(send_args)
            elif args[0] in ('-E', '-f', '-p', '-R', '-y', '-Z'):
                _eat(tui_args)
            elif args[0] in ('-d', '-D', '-F', '-m', '-n'):
                _eat(sys_args)
            elif args[0] == '--':
                send_args['--'] += args[1:]
                args = []
            elif args[0][:1] != '-':
                send_args['--'].append(args.pop(0))
            else:
                return COMMANDS['help'](wd, [], invalid=args[0])

        tui_exclusive = ('-f', '-p', '-y', '-Z')
        for a in (arg for arg in tui_exclusive if arg in tui_args):
            for b in tui_exclusive:
                if a != b and (b in tui_args):
                    raise Nonsense('Cannot %s and %s at once' % (a, b))

    except Nonsense as e:
        return COMMANDS['help'](wd, [], invalid=str(e))

    # This is our default mode of operation
    if not args and not tui_args and not send_args:
        tui_args['-y'] = True

    if tui_args:
        return CommandTUI(wd, sys_args, tui_args, send_args)
    elif send_args:
        return FakeDraftMain(sys_args, send_args)  # FIXME

    # Fallback
    return COMMANDS['help'](wd, [])


COMMANDS = {
    'default': CommandMuttalike,
    'start': CommandStart,
    'stop': CommandStop,
    'tui': CommandTUI}


def Main(args):
    from ..config.paths import DEFAULT_WORKDIR
    from ..config import configure_logging
    wd = DEFAULT_WORKDIR()

    command = 'default'
    if len(args) > 0 and args[0][:1] != '-' and '@' not in args[0]:
        command = args.pop(0)

    if command not in COMMANDS:
        from .cli import CLI_COMMANDS
        COMMANDS.update(CLI_COMMANDS)

    configure_logging(profile_dir=wd, level=logging.DEBUG)
    command = COMMANDS.get(command)
    if command is not None:
        command(wd, args)
    else:
        COMMANDS['help'](wd, args)
