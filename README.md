# Welcome to Moggie!

Moggie might become Mailpile 2.0 - a Python 3 rewrite of the original
Mailpile (<https://www.mailpile.is/>).

A fast, secure e-mail client? Someday!


## Project status

[Issue #1 tracks progress and gives a rough idea of what is planned](https://github.com/BjarniRunar/moggie/issues/1).


## Hacking Micro-Howto

First, brace yourself for nothing working: see Project Status above.

Install Moggie:

   1. apt install git python3-{cryptography,urwid,websockets,numpy}
   2. git clone https://github.com/BjarniRunar/moggie
   3. cd moggie
   4. git submodule init
   5. git submodule update

Next, grab some e-mail to play with, in Maildir or mbox format. For example,
browse around <https://lists.apache.org/> and download monthly archives, e.g.
[dev@age.apache.org for 2022-01](https://lists.apache.org/api/mbox.lua?list=dev@age.apache.org&date=2022-01).

Play with Moggie:

    # The following commands run from the root of the git repo
    cd /path/to/moggie

    # Hard-code a dummy passphrase for Moggie. Note this ends up written
    # in the clear to the filesystem: don't use a real passphrase.
    python3 -m moggie config set Secrets passphrase 'this-is-insecure'

    # Read some mail using Moggie:
    python3 -m moggie -f /path/to/archive.mbox

    # Start the Moggie background process
    python3 -m moggie start

    # Import mail into Moggie:
    python3 -m moggie import /path/to/archive.mbox

    (... wait a bit, check top to see if Moggie is busy ...)

    # Stop the Moggie background process
    python3 -m moggie stop

Moggie will by default write data to `~/.local/share/Moggie/default` - you
will probably want to delete that folder now and then.


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

