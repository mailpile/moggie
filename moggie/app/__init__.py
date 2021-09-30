import os
import sys


def CommandStart(wd, args):
    wait = 'wait' in args
    if wait:
        args.remove('wait')

    if args and args[0] == 'recovery_svc':
        from ..workers.recovery_svc import RecoverySvcWorker
        worker = RecoverySvcWorker.FromArgs(wd, args[1:])
    else:
        from ..workers.app import AppWorker
        worker = AppWorker.FromArgs(wd, args[0:])

    if worker.connect():
        if wait:
            worker.join()
        else:
            print('Running %s in the background' % worker.KIND)
        os._exit(0)
    else:
        sys.exit(1)


def CommandStop(wd, args):
    if args and args[0] == 'recovery_svc':
        from ..workers.recovery_svc import RecoverySvcWorker
        worker = RecoverySvcWorker.FromArgs(wd, args[1:])
    else:
        from ..workers.app import AppWorker
        worker = AppWorker.FromArgs(wd, args[0:])

    if worker.connect(autostart=False):
        result = worker.quit()
        if result and result.get('quitting'):
            sys.exit(0)
    sys.exit(1)


def CommandTUI(wd, args, sys_args=None, tui_args=None, send_args=None):
    from . import tui
    return tui.Main(wd, args)


def CommandMuttalike(wd, args):
    """
    This command will be a shim which implements many of the same
    command line options as mutt, in a moggie way.
    """
    from ..email.draft import FakeDraftMain, MessageDraft
    single = ('-D', '-E', '-R', '-h', '-n', '-p', '-v' '-vv' , '-y', '-z', '-Z')
    def _eat():
        if args[0] in single:
            return [args.pop(0)]
        eating = args[:2]
        args[:2] = []
        return eating

    sys_args = []
    tui_args = []
    send_args = []
    while args:
        if args[0] == '-h':
            return COMMANDS['help'](wd, args[1:])
        elif args[0] in MessageDraft.ALL_CLI_ARGS:
            send_args += _eat()
        elif args[0] in ('-E', '-f', '-p', '-R', '-y', '-Z'):
            tui_args += _eat()
        elif args[0] in ('-d', '-D', '-F', '-m', '-n'):
            sys_args += _eat()
        elif args[0] == '--':
            send_args += args
            args = []
        elif args[0][:1] != '-':
            send_args.append(args.pop(0))
        else:
            return COMMANDS['help'](wd, [], invalid=args[0])

    # This is our default mode of operation
    if not args and not tui_args and not send_args:
        tui_args = ['-y']

    if tui_args:
        return CommandTUI(wd, [], sys_args, tui_args, send_args)
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
    wd = DEFAULT_WORKDIR()

    command = 'default'
    if len(args) > 0 and args[0][:1] != '-' and '@' not in args[0]:
        command = args.pop(0)

    if command not in COMMANDS:
        from .cli import CLI_COMMANDS
        COMMANDS.update(CLI_COMMANDS)

    command = COMMANDS.get(command)
    if command is not None:
        command(wd, args)
    else:
        COMMANDS['help'](wd, args)
