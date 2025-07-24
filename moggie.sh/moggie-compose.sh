#!/bin/bash
#
# Moggie-base composer, mockup
#
editor=${VISUAL:-${EDITOR:-vi}}
draft=$1

get_draft_headers() {
    sed -e '/^%/,$ d' < $draft
}

get_header() {
    header_name=${1:-NONE}
    get_draft_headers |grep ^$header_name |head -1 |cut -f2- -d" "
}

make_blank_draft() {
    moggie email \
        --from='fixme@example.org' \
        --subject='(your subject)' \
        --format=rfc822 \
        | sed -e 's/\r//g'

#       --with-editing-headers=Y
}

if [ "$draft" = "" ]; then
    # Generate draft template if none is provided
    draft=$(mktemp)
    make_blank_draft >$draft
fi
if [ ! -e $draft ]; then
    # FIXME: is $draft a remote draft ID? In that case, download it to
    #        a local directory of message + attachments. This implies
    #        we want to be able to view messages as tar streams with
    #        one directory per e-mail.
    #
    # ... and also implies we want to generate e-mails from directories!
    echo "FIXME: Search for a remote draft" && exit 1
fi

# Debugging
set -e
set -x

# Edit
$editor $draft

# Ask about attachments

# Generate e-mail to drafts

# Ask whether to send
want_send="$(get_header X-Send | cut -b1)"
if [ "$want_send" = "" ]; then
    echo -n "Send message? [yN]"
    read want_send
fi
if [ "$want_send" != "" -a "$want_send" != "n" -a "$want_send" != "N" ]; then
    echo "Should send message: $want_send"
fi

