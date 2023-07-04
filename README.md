# Welcome to Moggie!

Moggie might become Mailpile 2.0 - a Python 3 rewrite of the original
Mailpile (<https://www.mailpile.is/>).

A fast, secure e-mail client? Someday!


## Project status

[Issue #1 tracks progress and gives a rough idea of what is planned](https://github.com/mailpile/moggie/issues/1).


## Contributing

For now, please don't?

This project is in "quiet mode" (not quite stealth), because I want to
get it to a certain level of maturity before I engage with users,
testers and other developers. The only feedback I'm interested in at the
moment is positive "wow that's awesome!" reinforcement. Which may not be
justified just yet. ;-)

The work is published here in case I get hit by a bus, and so the
Mailpile community (who directly and indirectly funded my work) can see
what I am up to.



## Architectural overview

Moggie currently masquerades as a "TUI" (text-(G)UI) app, but behind the
scenes it is a collection of microservices using HTTP-based RPC calls to
talk to each other. There are microservices for the search engine, the
metadata store, filesystem operations, PGP operations, and IMAP
connections. A master "application logic" process implements an API and
is responsible for coordination and access controls.

Moggie "clients" send one-off HTTP requests, or establish a longer lived
websocket to the "app" worker. The plan is for Moggie to support a web
user interface (like Mailpile), and integrate
[PageKite](https://pagekite.net/) for easy remote access and
collaboration.

Structured data is stored on disk using binary records, most of which is
AES encrypted. Moggie's native "mailbox" format is a ZIP archive
containing a Maildir directory structure, which may be AES encrypted
and/or compressed. Moggie's encryption keys are currently left in the
clear in the config file until I've figured out the UX and integrated
[Passcrow](https://passcrow.org/) for password recovery.


## Hacking Micro-Howto

First, brace yourself for nothing working: see Project Status above.

Install Moggie:

   1. apt install git python3-{numpy,cryptography,pycryptodome,urwid}
                      python3-{appdirs,setproctitle,pyqrcode,packaging}
                      python3-{aiosmtplib,aiodns,dkim,pgpy,pgpdump}
   2. git clone https://github.com/mailpile/moggie
   3. cd moggie
   4. git submodule init
   5. git submodule update

Next, grab some e-mail to play with, in Maildir or mbox format. For example,
browse around <https://lists.apache.org/> and download monthly archives, e.g.
[dev@age.apache.org for 2022-01](https://lists.apache.org/api/mbox.lua?list=dev@age.apache.org&date=2022-01).

Play with Moggie:

    # The following commands run from the root of the git repo
    cd /path/to/moggie

    # Read some mail using Moggie:
    python3 -m moggie -f /path/to/archive.mbox

    # Start the Moggie background process/server
    python3 -m moggie start

    # Import mail into Moggie:
    python3 -m moggie import /path/to/archive.mbox

    (... wait a bit, check top to see if Moggie is busy ...)

    # Stop the Moggie background process
    python3 -m moggie stop

Moggie will write data to **`~/.local/share/Moggie/default`**.

You will probably want to delete that folder now and then, or at least
the contents of the various subdirectories, since the format of Moggie's
on-disk data structures is still in flux and obsolete data may cause
weird issues.

The data includes logs (in the subdirectory named `logs`) which may be
useful for debugging. Be warned it may also leak your secrets if you
increase the logging verbosity. There is also a `config.rc`.



## Credits and License ##

Bjarni R. Einarsson (<https://bre.klaki.net/>) is currently the sole
developer of Moggie.

It is built on the work of the Mailpile community, in particular:

- Bjarni R. Einarsson (<http://bre.klaki.net/>)
- Brennan Novak (<https://brennannovak.com/>)
- Smari McCarthy (<http://www.smarimccarthy.is/>)
- Lots more, run `git shortlog -s` for a list! (Or check
  [GitHub](https://github.com/mailpile/Mailpile/graphs/contributors).)
- [Our community of backers](https://www.mailpile.is/#community).

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation. See the file `COPYING.md` for details.

