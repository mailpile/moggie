TOPICS = {
#############################################################################
                             '_synopsis': """\
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
 * moggie tag <+TAG|-TAG> <terms>    Add or remove tags from search results

 * moggie help how-to-search         How to write moggie search terms
 * moggie help topics                List all available commands and topics
 * moggie help <command>             Learn more about a specific command

Bearing in mind that imitation is the sincerest form of flattery, Moggie tries
to implement (most of) the command-line interfaces of both mutt and notmuch.

*) Commands prefixed with an asterisk (*) can also be invoked using the faster
   `lots` commmand-line tool, instead of `moggie` itself.
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
        moggie search in:inbox NOT tag:unread
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
    `spam` or `trash`, unless they are explicitly mentioned as one of the
    search terms.
    """}
