import sys

from .command import CLICommand
from .admin import CommandWelcome, CommandGrant, CommandContext
from .admin import CommandUnlock, CommandEnableEncryption, CommandImport
from .notmuch import CommandSearch, CommandAddress, CommandShow, CommandCount
from .notmuch import CommandConfig, CommandTag
from .notmuch import CommandEmail, CommandCompose, CommandReply, CommandForward
from .help import TOPICS


class CommandHelp(CLICommand):
    """# moggie help [command]

    Help on how to use moggie (on the command-line). Run `moggie help`
    without any arguments for a quick introduction and list of topics.

    Options:

    %(OPTIONS)s

    """
    NAME = 'help'
    ROLES = None
    CONNECT = False
    WEBSOCKET = False
    WEB_EXPOSE = True
    OPTIONS = [[
        ('--format=', ['text'], 'Output format; text*, json, html, sexp'),
    ]]

    def configure(self, args):
        self.arglist = self.strip_options(args)
        self.mimetype = {
            'text': 'text/plain; charset=utf-8',
            'html': 'text/html; charset=utf-8',
            'json': 'application/json',
            }.get(self.options['--format='][-1], 'application/octet-stream')
        return []

    def help_options(self, cls):
        def _section(opt_list):
            longest = 10
            for opt, ini, comment in opt_list:
                longest = max(len(opt or '')+3, longest)
            fmt = '    %%-%ds %%s' % longest
            for opt, ini, comment in opt_list:
                if opt and comment:
                    if opt.endswith('='):
                        opt += 'X'
                    yield fmt % (opt, comment)

        help_hash = {
            'OPTIONS': '\n\n'.join(
                '\n'.join(_section(s)) for s in cls.OPTIONS)}

        for section in cls.OPTIONS:
            first = section[0]
            if not first[0] and first[-1]:
                help_hash[first[-1]] = '\n'.join(_section(section))

        return help_hash

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
                 .replace('\n    %(', '\n%(')
                 .replace('\n    # ', '\n# ')
                 .replace('\n    ## ', '\n## ')
                 .replace('\n    ### ', '\n  ### ')
                 .replace('\n    ', '\n  '))

        def _html_safe(text):
            return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        fmt = self.options['--format='][-1]
        happy = True
        chunks = []
        output = {}
        def _print(*chunk_list):
            chunks.append(''.join(chunk_list))

        if not self.arglist:
            self.arglist = ['_synopsis']
        if len(self.arglist) == 1:
            arg = self.arglist[0]
            cmd = CLI_COMMANDS.get(arg)

            arg_fmt = '%s:%s' % (arg, fmt)
            if arg_fmt in TOPICS:
                output[fmt] = TOPICS[arg_fmt]
            output['title'] = 'moggie help %s' % arg

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

            elif arg_fmt in TOPICS:
                _print(_unindent(TOPICS[arg_fmt]), '\n')

            elif arg in TOPICS:
                _print(_unindent(TOPICS[arg]), '\n')

            elif (cmd is not None) and cmd.__doc__:
                helptext = _unindent(cmd.__doc__.strip())
                _print(helptext % self.help_options(cmd), '\n')

            else:
                _print("""\
Unknown topic: %s

Try `moggie help topics` for a list of what help has been written.
""" % arg)
                happy = False


        output['text'] = '\n'.join(chunks)
        if fmt == 'text':
            return self.print(output['text']) and happy
        elif 'html' not in output:
            import markdown
            # FIXME: Linkify moggie commands?
            output['html'] = markdown.markdown(output['text'])
        if fmt == 'html':
            self.print_html_start(title=output['title'])
            self.print(output['html'])
            self.print_html_end()
        elif fmt == 'json':
            self.print_json(output)
        elif fmt == 'sexp':
            self.print_json(output)
        return happy


CLI_COMMANDS = {
    CommandWelcome.NAME: CommandWelcome,
    CommandGrant.NAME: CommandGrant,
    CommandContext.NAME: CommandContext,
    CommandAddress.NAME: CommandAddress,
    CommandSearch.NAME: CommandSearch,
    CommandShow.NAME: CommandShow,
    CommandCount.NAME: CommandCount,
    CommandEmail.NAME: CommandEmail,
    CommandCompose.NAME: CommandCompose,
    CommandForward.NAME: CommandForward,
    CommandReply.NAME: CommandReply,
    CommandTag.NAME: CommandTag,
    'import': CommandImport,
    'encrypt': CommandEnableEncryption,
    'unlock': CommandUnlock,
    'config': CommandConfig,
    CommandHelp.NAME: CommandHelp}
