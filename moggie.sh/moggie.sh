#!/bin/bash
#
MOGGIE_SH_VIEWER=${MOGGIE_SH_VIEWER:-"less -F -X -s"}
MOGGIE_SH_CHOOSER=${MOGGIE_SH_CHOOSER:-"fzf -i -e --layout=reverse-list --no-sort"}
MOGGIE_SH_MAILLIST=${MOGGIE_SH_MAILLIST:-"$MOGGIE_SH_CHOOSER --with-nth=2.."}


##[ Prerequisites ]###########################################################

if [ "%(which fzf)" = "" ]; then
    echo 'Please install fzf!' 
    exit 1
fi


##[ A mail client! ]##########################################################

cat <<tac
==============================================================================
Welcome to moggie.sh!

This is a bare-bones e-mail client written as a shell script, using the moggie
CLI as a back end. It's a proof of concept more than anything, but if you find
it useful, more power to you!
tac

TEMPFILE=$(mktemp)

# FIXME: Make this a moggie status call?
if [ ! -f ~/.local/share/Moggie/default/workers/app.url ]; then
    moggie start
    moggie_stop () {
        moggie stop
    }
    trap moggie_stop EXIT
fi

PROMPT_ROOT="q:Quit t:Tags c:Compose"
PROMPT_EMAIL="q:Quit r:Reply a:Archive b:Shell"

SEARCH="$@"
PROMPT=$PROMPT_ROOT
while true; do
    if [ "$SEARCH" = "" ]; then
        SEARCH="in:inbox"
    fi

    echo -e '\n'\
==============================================================================
    echo -n "$PROMPT OR search [$SEARCH]> "
    read NEXT
    case $NEXT in
        a) echo 'Please archive' && sleep 2
           SEARCH=""
        ;;
        b) moggie search "$SEARCH $CHOSEN" --format=msgdirs >$TEMPFILE
           DIRNAME=$(dirname $(head -c 1024 $TEMPFILE |tar tfz - 2>/dev/null |head -2 |tail -1))
           if [ "$DIRNAME" != "" ]; then
               if tar xfz $TEMPFILE 2>/dev/null; then
               (
                   cd $DIRNAME
                   echo -e '\n'\
============================'[ spawning shell - exit to return to moggie.sh ]'==
                   grep -a -e '^Subject:' -e '^From:' message.txt
                   echo
                   ls -lh
                   echo
                   bash --init-file <(echo '. ~/.bashrc; PS1="email $ "') -i
               )
               fi
           fi
           rm -rf ./$(dirname $DIRNAME)
        ;;
        c) echo 'Please compose new message' && sleep 2
           SEARCH=""
        ;;
        q) break
        ;;
        r) echo 'Please reply' && sleep 2
           SEARCH=""
        ;;
        t) SEARCH=$(moggie search --output=tags all:mail |$MOGGIE_SH_CHOOSER)
        ;;
        *) [ "$NEXT" != "" ] && SEARCH="$NEXT"
        ;;
    esac

    if [ "$SEARCH" != "" ]; then
        moggie search "$SEARCH" |sed -e 's/ /\t/' >$TEMPFILE
        CHOSEN=$($MOGGIE_SH_MAILLIST <$TEMPFILE |cut -f1)

        # FIXME: Here we need some friendlier options
        #        Instead of --with-headers=Y, a header summery
        #        Instead of --with-html-text=Y, a best-effort text display
        if [ "$CHOSEN" != "" ]; then
            clear
            moggie show "$SEARCH $CHOSEN" |$MOGGIE_SH_VIEWER
            PROMPT=$PROMPT_EMAIL
        else
            PROMPT=$PROMPT_ROOT
        fi

    else
        PROMPT=$PROMPT_ROOT
    fi

done
echo -e '\n'\
'=================================================================[ Goodbye! ]='
