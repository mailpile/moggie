import sys

from .command import CLICommand
from .admin import CommandGrant, CommandContext
from .admin import CommandUnlock, CommandEnableEncryption
from .admin import CommandImport, CommandExport
from .notmuch import CommandSearch, CommandAddress, CommandShow, CommandCount, CommandTag
from .notmuch import CommandConfig
from .help import TOPICS


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
    OPTIONS = {'--format=': ['text']}

    def configure(self, args):
        self.arglist = self.strip_options(args)
        self.mimetype = {
            'text': 'text/plain; charset=utf-8',
            'html': 'text/html; charset=utf-8',
            'json': 'application/json',
            }.get(self.options['--format='][-1], 'application/octet-stream')
        return []

    async def run(self):
        global CLI_COMMANDS

        # FIXME: The TOPICS variable is our key to i18n of the documentation;
        #        it will override anything builtin, if the topic exists in the
        #        dict.

        def _wrap(line, prefix=''):
            words = line.split(' ')
            lines = [prefix]
            for word in words:
                if len(lines[-1]) + len(word) > 72:
                    lines.append(prefix)
                lines[-1] += (word + ' ')
            return '\n'.join(lines).rstrip()

        def _unindent(lines):
            return (lines
                 .replace('\n    # ', '\n# ')
                 .replace('\n    ## ', '\n## ')
                 .replace('\n    ### ', '\n  ### ')
                 .replace('\n    ', '\n  '))

        def _html_safe(text):
            return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        chunks = []
        output = {}
        def _print(*chunk_list):
            chunks.append(''.join(chunk_list))

        if not self.arglist:
            self.arglist = ['_synopsis']
        if len(self.arglist) == 1:
            arg = self.arglist[0]
            cmd = CLI_COMMANDS.get(arg)
            if arg == 'topics':
                cmds = ', '.join(sorted(
                    k for k in CLI_COMMANDS.keys()
                    if CLI_COMMANDS[k].__doc__ or k in TOPICS))
                topics = ', '.join(sorted(
                    t for t in TOPICS.keys()
                    if t not in CLI_COMMANDS and not t[:1] == '_'))

                _print('# moggie help topics\n')
                if cmds:
                    _print('## Commands:\n\n' + _wrap(cmds, '  '), '\n')
                if topics:
                    _print('## Other topics:\n\n' + _wrap(topics, '  '), '\n')

            elif arg in TOPICS:
                _print(_unindent(TOPICS[arg]), '\n')

            elif cmd is not None and cmd.__doc__:
                _print(_unindent(cmd.__doc__.strip()), '\n')

            else:
                _print("""\
Unknown topic: %s

Try `moggie help topics` for a list of what help has been written.
""" % arg)
                return False


        fmt = self.options['--format='][-1]
        output['text'] = '\n'.join(chunks)
        if fmt == 'text':
            return self.print(output['text'])
        else:
            # FIXME: Linkify moggie commands?
            output['html'] = (
                '<pre class="moggie_help">' +
                _html_safe(output['text']) +
                '</pre>')
        if fmt == 'html':
            self.print(output['html'])
        elif fmt == 'json':
            self.print_json(output)
        elif fmt == 'sexp':
            self.print_json(output)


CLI_COMMANDS = {
    CommandGrant.NAME: CommandGrant,
    CommandContext.NAME: CommandContext,
    CommandAddress.NAME: CommandAddress,
    CommandSearch.NAME: CommandSearch,
    CommandShow.NAME: CommandShow,
    CommandCount.NAME: CommandCount,
    CommandTag.NAME: CommandTag,
    CommandExport.NAME: CommandExport,
    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'unlock': CommandUnlock,
    'config': CommandConfig,
    CommandHelp.NAME: CommandHelp}
