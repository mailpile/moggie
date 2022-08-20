from .admin import CommandUnlock, CommandImport, CommandEnableEncryption
from .notmuch import CommandSearch, CommandAddress, CommandConfig
from .help import TOPICS


# FIXME: Here we really should think about i18n
def CommandHelp(wd, args):
    """# moggie help [command]

    Help on how to use Moggie (on the command-line). Run `moggie help`
    without any arguments for a quick introduction and list of topics.
    """
    global CLI_COMMANDS

    def _wrap(line, prefix=''):
        words = line.split(' ')
        lines = [prefix]
        for word in words:
            if len(lines[-1]) + len(word) > 72:
                lines.append(prefix)
            lines[-1] += (word + ' ')
        return '\n'.join(lines).rstrip()

    if len(args) == 1:
        arg = args[0]
        cmd = CLI_COMMANDS.get(arg)
        if arg == 'topics':
            cmds = ', '.join(sorted(
                k for k in CLI_COMMANDS.keys()
                if CLI_COMMANDS[k].__doc__ or k in TOPICS))
            topics = ', '.join(sorted(
                t for t in TOPICS.keys()
                if t not in CLI_COMMANDS))

            print('# moggie help topics\n')
            if cmds:
                print('## Commands:\n\n' + _wrap(cmds, '  ') + '\n')
            if topics:
                print('## Other topics:\n\n' + _wrap(topics, '  ') + '\n')

        elif arg in TOPICS:
            print(TOPICS[args], '\n')

        elif cmd is not None and cmd.__doc__:
            print(cmd.__doc__.strip(), '\n')

        else:
            print("""\
Unknown topic: %s

Try `moggie help topics` for a list of what help has been written.
""" % arg)

    else:
        print("""\
# Welcome to Moggie!

Some useful commands:

  moggie start [--cli|--wait]       Launch the Moggie background workers
  moggie stop                       Stop Moggie

  moggie encrypt                    Enable encryption of local data
  moggie unlock                     Unlock a running, encrypted Moggie              
  moggie compact                    Perform housekeeping to free up disk space

  moggie import </path/to/mailbox>  Add some mail to the search index
  moggie search <terms>             Search for mail, lists subjects
  moggie address <terms>            Search for mail, lists senders

  moggie help <command>             Learn more about a specific command.
  moggie help topics                List all available topics

Bearing in mind that imitation is the sincerest form of flattery, Moggie tries
to implement (most of) the command-line interfaces of both mutt and notmuch.
""")


CLI_COMMANDS = {
    'address': CommandAddress,
    'search': CommandSearch,
    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'unlock': CommandUnlock,
    'config': CommandConfig,
    'help': CommandHelp}
