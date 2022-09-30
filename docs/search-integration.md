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
   6. Notes for developers

Each of these steps is discussed in further detail below.

Notes:

   * `moggie` is used below as a shorthand for `python3 -m moggie`. Once
     the app has matured, it will have an installation procedure which
     creates the `moggie` command and places it on the user's PATH.)
   * These are the instructions for moggie *users* who want to enable such
     3rd party integration. If you are a developer of a 3rd party app and
     would like to integrate with moggie, skip to the end!


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


## 6. Notes for developers

Moggie is at an early stage of development, and its APIs are still in
flux. It does not yet have a user-facing web interface, only APIs
which are not yet fully stable and only partially documented.

However, moggie's command-line interface aims to be mostly compatibile
with the excellent `notmuch` search tool - and most of moggie's
command-line interface is implemented as a web API.

(Moggie's fast CLI tool, `lots`, is actually just a thin shell wrapper
around curl. Lots is not notmuch.)


### Searching for messages

The API endpoint for searching is `/cli/search`, which takes the
query string arguments `q=<search terms>` and `format=<format>`
(among others). Examples:

    http://localhost:8025/TOKEN/cli/search?q=bjarni&format=json

    http://localhost:8025/TOKEN/cli/search?q=hello%20world&format=text

The JSON output will look something like this:

    [
      {
        "thread": "00016833",
        "timestamp": 1236872620,
        "date_relative": "2009-03-12",
        "matched": 1,
        "total": 1,
        "files": 1,
        "authors": "Joe Random",
        "subject": "Welcome to Funland!",
        "query": ["id:432.123.e1c951234123422e0d424b4f9a4ba73", null],
        "tags": ["inbox", "unread"]
      },
      ...
    ]

(This format is deliberately similar to the JSON output of the `notmuch
search` command, and in the interest of compatibility, moggie's search
takes most of the same arguments as notmuch.)

In particular, note the `query` element of the JSON object; it is a list
of IDs which can be passed to `/cli/show` to fetch the message itself.

For more details about what kinds of searches can be performed, consult
the output of `moggie help search`. The man page for `notmuch-search` may
also be of use.


### Fetching entire messages or threads

The API endpoint for downloading an e-mail is `/cli/show`.

Like `notmuch show`, moggie's show method will output messages matching
arbitrary search queries.

However, the most common use for this endpoint is to display a message
or thread after discovering it using the `/cli/search` method, by
passing it the ID provided in the search result. Depending on how the
search was performed, the IDs may be short (id:nnn) or long
(id:nnn.mmm.ssss).

Long IDs are cryptographically signed and can be used with `/cli/show`
without an authentication token, which will allow an app to pass message
URLS on to the user, without leaking the access token itself. Even
though moggie does not yet have a user-facing web interfaace, this may
still be useful today for dowloading an `mbox` formatted collection of
matching messages. Signed IDs become invalid when the access token used
to generate them is revoked or expired.

These are example URLs including the token:

    http://localhost:8025/TOKEN/cli/show?q=id:1234&format=json

    http://localhost:8025/TOKEN/cli/show/id:1234?format=mbox

Examples without the token:

    http://localhost:8025/cli/show/id:1234.123.21341234124?format=json

    http://localhost:8025/cli/show/id:1234.123.21341234124

The available formats are `text`, `json` and `mbox`, with JSON
and mbox formats being most useful for integration.

The JSON format looks a bit like this:

    [            <-- This is a list of threads
     [           <-- This is a list of messages
      [          <-- This is a (message info, replies) pair
       {
        "id": "183081.16c6.dd22d258d24dab22507290b9d8c80767684e9175",
        "match": true,
        "timestamp": 1661171873,
        "date_relative": "2022-08-22",
        "headers": {
          "Subject: Hello, this is a test",
          "From": "Bjarni R. E. <bre@example.org>",
          "To": "Clever Jane <jane@example.org",
          "Date": "Mon, 22 Aug 2022 12:37:53 -0000"
        },
        "body": [
          {
            "id": 1,
            "content-type": "multipart/related",
            "content": [
              {
                "id": 2,
                "content-type": "multipart/alternative",
                "content": [
                  {
                    "id": 3,
                    "content-type": "text/plain",
                    "content": "Hello world!"
                  },
                  {
                    "id": 4,
                    "content-type": "text/html"
                  }
                ]
              }
            ]
          }
        ],
        "tags": [
          "inbox",
          "unread",
        ],
        "crypto": {}
       },
       [ ... ]   <-- Replies to this message would appear in this list
      ],
      ...
     ],
     ...
    ]

There is a significant amount of nesting, in order to represent both
the tree-structure of conversation threads, and the internal structure
of the e-mail.

By default, the JSON output will also include information about other
messages in the thread, even if they did not mach the original query.
Messages that did match will have `"match": True` set.

For more details about what kinds of searches can be performed, consult
the output of `moggie help show`. The man page for `notmuch-show` may
also be of use, since this method also strives to be compatible with
its notmuch counterpart.
