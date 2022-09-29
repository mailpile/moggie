# Moggie/Mailpile Search Integration

Moggie's search engine can be accessed by other applications, using a
HTTP-based API.

Due to the fact that e-mail (encrypted or not) can contain large amounts
of sensitive information, the user is given tools with which to scope
and manage which e-mails are exposed to external tools.

Using moggie as a search-engine for an external application can be
summarized as the following process:

   1. Import e-mail into moggie's database
   2. Ensure Moggie has a stable local or public URL
   3. Create a "Context", defining which e-mails (tags) can be accessed
   4. Grant access to the Context
   5. Inform the 3rd party tool of moggie's URL and access

Each of these steps is discussed in further detail below.

(Note: `moggie` is used below as a shorthand for `python3 -m moggie`.
Once the app has matured, it will have an installation procedure which
creates the `moggie` command and places it on the user's PATH.)


## 1. Import e-mail into moggie's database

Moggie currently only supports importing e-mail which is stored in
mailboxes on the local filesystem. To import such mail into the search
engine, use the `moggie import` command:

    $ moggie import /path/to/mailbox.mbx

    $ moggie import /path/to/Maildir

    $ moggie import --recurse /path/to/archives/


## 2. Ensure Moggie has a stable local or public URL

In order for 3rd party applications to integrate with moggie, they need to
know how to make contact with moggie's built-in HTTP server.

This server will by default listen on a port chosen at random every time the
app launches; which is fine if the 3rd party app has access and knows how to
read the current URL from the worker's status file:

    cat ~/.local/share/Moggie/default/workers/app.url

Since this may not be possible for various reasons (the 3rd party app may
run on a different machine or may lack permissions to access the user's
private files), usually you will want to configure moggie to always use
either the same local port (for local integration) and/or a stable public
URL (for remote access).

### To enforce a stable local port:

Shut down moggie and open the configuration file in your favorite
editor:

    python3 -m moggie stop

    vim ~/.local/share/Moggie/default/config.rc

Add a `port` setting to the `[App]` section. For example:

    [App]
    port = 8025

Then when moggie is restarted (`python3 -m moggie start`), the application
server should listen on `http://localhost:8025/` for API requests.

### To enable a stable public address:

Register for an account with <https://pagekite.net/> and create a kite
name for moggie, e.g. `moggie-USER.pagekite.me`.

Shut down moggie and open the configuration file in your favorite
editor:

    python3 -m moggie stop

    vim ~/.local/share/Moggie/default/config.rc

Add a `kite_name` and `kite_secret` settings to the `[App]` section. For
example:

    [App]
    kite_name = moggie-USER.pagekite.me
    kite_secret = YOUR_SECRET_HERE

Then, when moggie is restarted (`python3 -m moggie start`), the application
server should listen on `https://moggie-USER.pagekite.me/` for API requests.

**IMPORTANT:** Moggie does not yet have end-to-end TLS support; the HTTPS
encryption and decryption currently takes place at the PageKite relay, which
means the relay is a man in the middle and sees cleartext data. This will be
addressed in future versions of the app.


## 3. Create a "Context", defining which e-mails (tags) can be accessed

A "Context" defines a logical grouping of tags, settings and e-mail accounts.
Remote access is granted to one or more contexts, thus limiting which content
the 3rd party application has access to.

    $ moggie context create "Yum" --with-standard-tags
    $ moggie context update "Yum" --tag="icecream" --tag="cake"

This will allow users or applications with access to the Yum context,
to access messages which have been tagged with the tags "icecream" or "cake".
The standard tags ("inbox", "unread", "trash", etc.) will also be visible,
but only the subset which is also tagged as icecream or cake.

To make more tags visible, but not required:

    $ moggie context update "Yum" --show-tag="flagged"

If we later decide we hate cake, we can remove it from the context like so:

    $ moggie context update "Yum" --remove-tag="cake"

If the user wants to be further narrow down the search results by requiring
or excluding specific terms, that can be done using the forbid and require
arguments:

    $ moggie context update "Yum" --require="dates:2022"
    $ moggie context update "Yum" --forbid="is:encrypted"
    $ moggie context update "Yum" --forbid="vegetables" --forbid="veggies"

Note: The more tags and requirements are added, the more work the search
engine has to do on every action, which may impact performance. Searching
for tags is generally quicker than keyword searches, so using filters at
to tag at import time will be more performant.

(Note: The author does not recommend excluding vegetables from your diet,
please eat your greens.)

(TODO: Explain and demo tag namespaces; the tl;dr is they allow contexts
to have separate tags which appear to share the same names, so inbox in
one context is not the same as the inbox in another.)


## 4. Grant access to the Context

Access is granted to a moggie Context using the `moggie grant` command.

This will grant normal "user" access (read, write, search, tag etc.):

    $ moggie grant create "Bjarni" user --context="Yum"

... or for read-only access:

    $ moggie grant create "Bjarni" guest --context="Yum"

In both cases, the tool will output a summary of the current granted
access, and a URL for use in the next step.

To remove Bjarni's access to Yum:

    $ moggie grant update "Bjarni" none --context="Yum"

To remove Bjarni's access entirely:

    $ moggie grant remove "Bjarni"

(Note: See `moggie help grant` for details about more granular
access controls, what roles exist etc.)


## 5. Inform the 3rd party tool of moggie's URL and access

In order to allow the 3rd party application to access moggie as "Bjarni",
you need to "log in" and copy the access token/URL to the app.

By default, access tokens are only valid for a week, but you will probably
want to set a longer "time to live".

Examples:

    $ moggie grant login "Bjarni" --output=urls --ttl=1y  # 1 year
    $ moggie grant login "Bjarni" --output=urls --ttl=1m  # 1 month
    $ moggie grant login "Bjarni" --output=urls --ttl=2w  # 2 weeks
    $ moggie grant login "Bjarni" --output=urls --ttl=5d  # 5 days

If you have already logged the user in, and just want to find the URL,
you can add `--output=urls` to the list operation:

    $ moggie grant list --output=urls           # Everyone
    $ moggie grant list "Bjarni" --output=urls  # Just Bjarni

In order to invalidate the URLs, you log the user out:

    $ moggie grant logout "Bjarni"



