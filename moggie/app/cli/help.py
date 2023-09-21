import sys
from .command import CLICommand

TOPICS = {
#############################################################################
                             '_synopsis': """\
# Welcome to moggie!

Some useful commands:

   moggie                            Launch the interactive e-mail client
   moggie start [--cli|--wait]       Launch the moggie background workers
   moggie unlock                     Unlock a running, encrypted moggie
   moggie stop                       Stop moggie
   moggie encrypt                    Enable encryption of local data
   moggie compact                    Perform housekeeping to free up disk space
   moggie import </path/to/mailbox>  Add some mail to the search index

 * moggie count [--multi] <terms>    Search, returns number of matches
 * moggie search <terms>             Search for mail, summarize results
 * moggie tag <+TAG|-TAG> <terms>    Add or remove tags from search results

 * moggie help how-to-search         How to write moggie search terms
 * moggie help topics                List all available commands and topics
 * moggie help <command>             Learn more about a specific command

Bearing in mind that imitation is the sincerest form of flattery, moggie tries
to implement (most of) the command-line interfaces of both mutt and notmuch.

*) Commands prefixed with an asterisk (*) can also be invoked using the faster
   `lots` commmand-line tool, instead of `moggie` itself.
""",
#############################################################################
                             '_synopsis:html': """\
<h1>Welcome to moggie!</h1>

<p>Moggie is a search engine for email, which aims to eventually be a full
blown mail client.</p>

<p>... things and stuff!</p>

""",
#############################################################################
                             'how-to-search': """\
# moggie help search-syntax

    Moggie is built on a powerful a search engine which is used both for
    finding and organizing large volumes of email messages and threads. The
    engine supports searching for keywords found in the message content, as
    well as attributes and metadata (such as email addresses, the presence
    or lack of attachments, or read/unread state).

    In addition to search terms relating to the messages themselves, moggie
    allows the user to assign arbitrary tags (or labels) to messages or
    threads, and search using those. This is a powerful, flexible way to
    organize large volumes of email.

    The search engine also supports compartmentalization, which means
    depending on what kind of access has been granted, searches may only
    be operating on a restricted subset of messages.

    Most of the `moggie` command-line commands act on results of a search,
    and searching should also a prominent feature of more user-friendly
    applications built on top of this engine.

    The search syntax and features of the engine are described below.


## Basic search syntax

    The most basic search, is to specify one or more keywords, all of
    which must be present for a message to match:

        moggie search  hello world       # Emails with 'hello' and 'world'

    A rudimentary form of partial-word matching is supported, by including
    an asterisk '*' in the term:

        moggie search  "bjarn*"          #  Match Bjarni, Bjarna, Bjarney ..

    Note that, for performance reasons, if a large number of keywords match
    the pattern, results may be incomplete.

    Basic boolean operators are also supported:

        moggie search  hello AND world   # Must contain both words
        moggie search  hello OR world    # Must contain 'hello' or 'word'
        moggie search  hello NOT mars    # ... 'hello' but not 'mars'

    The operators AND, OR and NOT must be capitalized.

    The Mailpile 1.0 search syntax (prefixing keywords with  + instead of
    OR, or - instead of NOT) is also supported, and can be thought about
    as adding or removing search results from the set.

        moggie search  hello +world   # Must contain both words
        moggie search  hello -mars    # ... 'hello' but not 'mars'

    Finally, for more complex queries, parenthesis can be used to group
    terms and operators together:

        moggie search  "(hello AND (world OR planet)) NOT mars"

    (Note that if searcching on the shell, you may need to enclose
    queries containing the asterisk or parenthesis in quotes to
    avoid accidental shell globbing.)


## Special search terms

    In addition to searching the contents of e-mail, special prefixes can
    be used to search for specific metadata, headers, ranges of dates, or
    other types of information.

    Examples:

        moggie search from:bre@example.org
        moggie search in:inbox AND tag:read
        moggie search dates:2021..2022-06       # 1.5 years worth of e-mail
        moggie search hello NOT subject:hello   # Hello, not in the subject

    Each type of special search term has its own prefix, and a colon (:)
    separates the prefix from the term itself:


    ### in:<tag>, tag:<tag>

      Search for messages with a specific tag. The `in:` and `tag:` prefixes
      are equivalent.

    ### id:<id>, thread:<id>

      Search by moggie message- or thread-ID. Note that either search may
      return multiple messages; and `id:XXX` search will return multiple
      messages if threads are requested, and `thread:XXX` will always return
      all messages in a thread (unless the scope is narrowed by additional
      search terms).

      The JSON (and S-Expression) representations of search results
      differentiate between results which are direct hits (`match=True`) and
      indirect.

      (Note that the moggie API differs from notmuch, in that moggie IDs are
      numbers assigned by the search engine; notmuch uses the `Message-ID`
      header directly as an ID.)

    ### from:<word or e-mail address>

      Search by message sender (`From:` header). If the term contains an
      `@`-sign, it is treated as an email address and must match exactly,
      otherwise it is treated as a word that should appear somewhere in the
      name or email address of the sender.

    ### to:<word or e-mail address>

      Just like the from: prefix, but for searching by recipient (`To:`,
      `Cc:` headers).

    ### subject:<word>

      Search for words appearing in the subject line.

    ### date:<date or start..end>, dates:<date or start..end>

      Search for emails sent at a specific time (per the Date: header). The
      `date:` and `dates:` prefixes are equivalent.

      Dates should be formatted as `YYYY-MM-DD` (year, month, day), but
      partial dates are allowed (such as `date:2022` or `date:2022-10`)
      and can be used to match entire months or years.

      To search for a range of dates, use the syntax `YYYY-MM-DD..YYYY-MM-DD`,
      where month and day are again optional. Ranges are inclusive of both
      ends, so `dates:2022-01..2022-12-31` will match the entire year (but
      would be more concisely and efficiently expressed as `date:2022`).


    (FIXME: Document more prefixes)


## Suppressed results: Spam and Trash

    By default, the search engine will omit results which are tagged as
    `junk` or `trash`, unless they are explicitly mentioned as one of the
    search terms.
    """}


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
        ('--format=', [None], 'Output format; text*, json, html, sexp'),
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
        from moggie.app.cli import CLI_COMMANDS

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
                output[fmt] = TOPICS[arg_fmt].rstrip()
            output['title'] = 'moggie help %s' % arg

            if arg == 'topics':
                cmds = ', '.join(sorted(
                    k for k in CLI_COMMANDS.keys()
                    if CLI_COMMANDS.get(k).__doc__ or k in TOPICS))
                topics = ', '.join(sorted(
                    t for t in TOPICS.keys()
                    if t not in CLI_COMMANDS and not t[:1] == '_'))

                _print('# moggie help topics\n')
                if cmds:
                    _print('## Commands:\n\n' + _wrap(cmds, '  '), '\n')
                if topics:
                    _print('## Other topics:\n\n' + _wrap(topics, '  '), '\n')

            elif arg_fmt in TOPICS:
                _print(_unindent(TOPICS[arg_fmt].rstrip()), '\n')

            elif arg in TOPICS:
                _print(_unindent(TOPICS[arg].rstrip()), '\n')

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
        if fmt in ('text', None):
            # FIXME: Can we detect a terminal and launch less/more for
            #        the user automatially? Would be nice!
            return self.print(output['text']) and happy

        if 'html' not in output:
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
