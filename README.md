# Welcome to Moggie!

Moggie might become Mailpile 2.0 - a Python 3 rewrite of the original
Mailpile (<https://www.mailpile.is/>).

A fast, secure e-mail client? Someday!


## Project status

[Issue #1 tracks progress and gives a rough idea of what is planned](https://github.com/mailpile/moggie/issues/1).

[I am writing a series of blog posts about this project](https://www.mailpile.is/blog/2023-05-01_A_Mail_Client_in_Six_Steps.html).


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

Install Moggie on recent Debian:

   1. apt install git python3-{numpy,cryptography,pycryptodome,urwid,msgpack}
                      python3-{appdirs,setproctitle,pyqrcode,packaging}
                      python3-{aiosmtplib,aiodns,dkim,pgpy,pgpdump,markdown}
   2. git clone --recurse-submodules https://github.com/mailpile/moggie

Install Moggie on Raspbian 11:

   1. apt install git python3-{numpy,cryptography,pycryptodome,urwid,msgpack}
                      python3-{appdirs,setproctitle,pyqrcode,packaging}
                      python3-{pip,aiodns,dkim,pgpy,pgpdump,markdown}
   2. python3 -m pip install aiosmtplib
   3. git clone --recurse-submodules https://github.com/mailpile/moggie

*This also works for the latest Ubuntu, but you will need to give scary
sounding arguments to pip. Using the hybrid virtualenv method discussed below,
instead of system-wide pip, may be a better approach.*

Or, if you use nix:

   1. nix-shell -p python3Packages.{numpy,cryptography,pycryptodomex,urwid}
                   python3Packages.{appdirs,setproctitle,pyqrcode,packaging}
                   python3Packages.{aiosmtplib,aiodns,dkimpy,pgpy,pgpdump}
                   python3Packages.{markdown,msgpack} openssl git
   2. git clone --recurse-submodules https://github.com/mailpile/moggie

Or, if you prefer a virtualenv:

   1. git clone --recurse-submodules https://github.com/mailpile/moggie
   2. cd moggie
   3. python3 -m venv --system-site-packages .venv
   4. source .venv/bin/activate
   5. python3 -m pip install -r requirements.txt

Run the tests:

   * python3 -W ignore:ResourceWarning -m unittest


*(Note that the virtualenv method is somewhat prone to failure, since many
of moggie's dependencies are tricky to build from source. A hybrid approach
where as much is installed using the OS package manager as possible, and
pip only used for missing packages may be more likely to succeed.)*

Next, grab some e-mail to play with, in Maildir or mbox format. For example,
browse around <https://lists.apache.org/> and download monthly archives, e.g.
[dev@age.apache.org for 2022-01](https://lists.apache.org/api/mbox.lua?list=dev@age.apache.org&date=2022-01).

Play with Moggie:

    # The following commands run from the root of the git repo
    cd /path/to/moggie

    # Read some instructions
    python3 -m moggie help

    # Read some mail using Moggie:
    python3 -m moggie -f /path/to/archive.mbox

    # Or browse an IMAP account
    python3 -m moggie -y -f imap://user@domain@imap.example.org/

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

Moggie development is funded in part through the
[NGI0 Entrust Fund](https://nlnet.nl/entrust),
a fund established by [NLnet](https://nlnet.nl/)
with financial support from the European Commission's
[Next Generation Internet](https://ngi.eu/) programme. Thank you!

Moggie is built on the work of the Mailpile community, in particular:

- Bjarni R. Einarsson (<http://bre.klaki.net/>)
- Brennan Novak (<https://brennannovak.com/>)
- Smari McCarthy (<http://www.smarimccarthy.is/>)
- Lots more, run `git shortlog -s` for a list! (Or check
  [GitHub](https://github.com/mailpile/Mailpile/graphs/contributors).)
- [Our community of backers](https://www.mailpile.is/#community).

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU Affero General Public License as published by
the Free Software Foundation. See the file `COPYING.md` for details.
