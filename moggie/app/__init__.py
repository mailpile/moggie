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


def CommandTUI(wd, args):
    from . import tui
    return tui.Main(wd, args)


COMMANDS = {
    'start': CommandStart,
    'stop': CommandStop,
    'tui': CommandTUI}


def Main(args):
    from ..config.paths import DEFAULT_WORKDIR
    wd = DEFAULT_WORKDIR()

    command = 'tui'
    if len(args) > 0 and args[0][:1] != '-':
        command = args.pop(0)

    if command not in COMMANDS:
        from .cli import CLI_COMMANDS
        COMMANDS.update(CLI_COMMANDS)

    command = COMMANDS.get(command)
    if command is not None:
        command(wd, args)
    else:
        COMMANDS['help'](wd, args)
