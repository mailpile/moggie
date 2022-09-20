import sys

from .command import CLICommand
from .admin import CommandUnlock, CommandEnableEncryption
from .admin import CommandImport, CommandExport
from .notmuch import CommandSearch, CommandAddress, CommandCount, CommandTag
from .notmuch import CommandConfig
from .help import TOPICS


# FIXME: Here we really should think about i18n
class CommandHelp(CLICommand):
    """# moggie help [command]

    Help on how to use Moggie (on the command-line). Run `moggie help`
    without any arguments for a quick introduction and list of topics.
    """
    NAME = 'help'
    ROLES = None
    CONNECT = False
    WEBSOCKET = False
    WEB_EXPOSE = True

    def configure(self, args):
        self.arglist = args
        return []

    async def run(self):
        global CLI_COMMANDS

        def _wrap(line, prefix=''):
            words = line.split(' ')
            lines = [prefix]
            for word in words:
                if len(lines[-1]) + len(word) > 72:
                    lines.append(prefix)
                lines[-1] += (word + ' ')
            return '\n'.join(lines).rstrip()

        if len(self.arglist) == 1:
            arg = self.arglist[0]
            cmd = CLI_COMMANDS.get(arg)
            if arg == 'topics':
                cmds = ', '.join(sorted(
                    k for k in CLI_COMMANDS.keys()
                    if CLI_COMMANDS[k].__doc__ or k in TOPICS))
                topics = ', '.join(sorted(
                    t for t in TOPICS.keys()
                    if t not in CLI_COMMANDS))

                self.print('# moggie help topics\n')
                if cmds:
                    self.print('## Commands:\n\n' + _wrap(cmds, '  ') + '\n')
                if topics:
                    self.print('## Other topics:\n\n' + _wrap(topics, '  ') + '\n')

            elif arg in TOPICS:
                self.print(TOPICS[arg], '\n')

            elif cmd is not None and cmd.__doc__:
                self.print(cmd.__doc__.strip(), '\n')

            else:
                self.print("""\
Unknown topic: %s

Try `moggie help topics` for a list of what help has been written.
""" % arg)
                return False

        else:
            self.print("""\
# Welcome to Moggie!

Some useful commands:

   moggie start [--cli|--wait]       Launch the Moggie background workers
   moggie unlock                     Unlock a running, encrypted Moggie
   moggie stop                       Stop Moggie
   moggie encrypt                    Enable encryption of local data
   moggie compact                    Perform housekeeping to free up disk space
   moggie import </path/to/mailbox>  Add some mail to the search index

 * moggie count [--multi] <terms>    Search, returns number of matches
 * moggie search <terms>             Search for mail, summarize results
 * moggie address <terms>            Search for mail, lists senders

 * moggie help <command>             Learn more about a specific command.
 * moggie help topics                List all available topics

Bearing in mind that imitation is the sincerest form of flattery, Moggie tries
to implement (most of) the command-line interfaces of both mutt and notmuch.

*) Commands prefixed with an asterisk (*) can also be invoked using the faster
   `lots` commmand-line tool, instead of `moggie` itself.
""")


CLI_COMMANDS = {
    CommandAddress.NAME: CommandAddress,
    CommandSearch.NAME: CommandSearch,
    CommandCount.NAME: CommandCount,
    CommandTag.NAME: CommandTag,
    CommandExport.NAME: CommandExport,
    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'unlock': CommandUnlock,
    'config': CommandConfig,
    CommandHelp.NAME: CommandHelp}
