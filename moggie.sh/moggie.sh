#!/bin/bash
#
exec bash --init-file <(sed -e '1,/^exec/ d' $0) -i

##[ Setup ]###################################################################

[ -e ~/.bashrc ] && source ~/.bashrc

MOGGIE_SH_HOMEDIR=${MOGGIE_SH_HOMEDIR:-~/.local/share/Moggie/moggie.sh}
MOGGIE_SH_VIEWER=${MOGGIE_SH_VIEWER:-"less -F -X -s"}
MOGGIE_SH_CHOOSER=${MOGGIE_SH_CHOOSER:-"fzf -i -e --layout=reverse-list --no-sort"}
MOGGIE_SH_MAILLIST=${MOGGIE_SH_MAILLIST:-"$MOGGIE_SH_CHOOSER --multi --with-nth=2.."}
MOGGIE_TMP=$(mktemp)

GREEN="\033[1;32m"
YLLOW="\033[1;33m"
BBLUE="\033[1;36m"
WHITE="\033[1;37m"
RESET="\033[0m"

PROMPT_DIRTRIM=2
PS1_BASE="$? \[${YLLOW}\]moggie.sh \\w \[${RESET}\]\$ "
PS1="$PS1_BASE"

moggie_sh_divider() {
    echo -e "${BBLUE}==================================================================${RESET}"
}

moggie_sh_info() {
    echo -n -e "${BBLUE}"
    echo -n "$@"
    echo -e "${RESET}"
}

moggie_sh_done() {
    moggie_sh_divider
    case "$1" in
        download)
            moggie_sh_info "*** Downloaded to: $(dirname $(grep /message.txt $MOGGIE_TMP))"
            moggie_sh_divider
        ;;
        *)
            if [ "$MOGGIE_CHOICE" != "" ]; then
                echo "$MOGGIE_CHOICE" |cut -f2-
                moggie_sh_divider
            fi
        ;;
    esac
    if [ "$MOGGIE_CHOICE" != "" ]; then
        moggie_sh_info "Commands: v=view, d=download, r=reply, f=forward, s=search, q=quit"
    else
        moggie_sh_info "Commands: c=compose, t=tags, s=search, q=quit"
    fi
}

c() {
    if [ "$1" != "" -a -d "$1" -a -e "$1/message.txt" ]; then
        MOGGIE_SH_DRAFT="$(cd "$1" && pwd)"
    else
        if [ "$1" = "new" -o "$MOGGIE_SH_DRAFT" = "" ]; then
            MOGGIE_SH_DRAFT="$MOGGIE_SH_HOMEDIR/Drafts/$(date +%Y%m%d-%H%M)"
        fi
    fi

    # Make sure it exists, normalize access and directory names
    mkdir -p "$MOGGIE_SH_DRAFT"
    chmod go-rwx "$MOGGIE_SH_DRAFT" "$MOGGIE_SH_HOMEDIR/Drafts"
    pushd "$MOGGIE_SH_DRAFT" >/dev/null
    MOGGIE_SH_DRAFT="$(pwd)"

    if [ ! -e message.txt ]; then
        # FIXME: We should have moggie generate this, based on the
        #        preferences (to/from/etc) for the active context.
        cat <<tac >message.txt
To: You <you@example.org>
From: Me <me@example.org>
Subject: Draft e-mail


$(cat ~/.signature 2>/dev/null)
tac
    fi
    cp message.txt draft.txt
    cat <<tac >>draft.txt
==============================================================moggie-sh-snip====
Type your message above this line!

If you want to add attachments, exit the editor and copy them to:

  "$MOGGIE_SH_DRAFT"

Once you're happy, send the e-mail by typing \`send\` at the moggie.sh prompt.
tac

    ${VISUAL:-${EDITOR:-vi}} draft.txt
    sed -e '/====moggie-sh-snip====/,$ d' <draft.txt >message.txt
    rm -f draft.txt
    SUBJECT=$(grep ^Subject: message.txt \
        |head -1 \
        |cut -f2 -d:)

    popd >/dev/null 2>&1
    if [ "$SUBJECT" != "" ]; then
        NN="$(dirname "$MOGGIE_SH_DRAFT")/$(date +%Y%m%d-%H%M) $SUBJECT"
        [ "$MOGGIE_SH_DRAFT" != "$NN" ] \
            && mv "$MOGGIE_SH_DRAFT" "$NN" \
            && MOGGIE_SH_DRAFT="$NN"
    fi

    pushd "$MOGGIE_SH_DRAFT" >/dev/null
}

d() {
    IDS=$(echo $(echo "$MOGGIE_CHOICE" |cut -f1) |sed -e s'/ id:/ +id:/g' -e s'/ thread:/ +thread:/g')
    moggie search "$MOGGIE_SEARCH" "$IDS" --format=msgdirs |tar xvfz - >$MOGGIE_TMP
    moggie_sh_done download
}

alias q=exit

s() {
    MOGGIE_SEARCH="$@"
    MOGGIE_CHOICE=""
    moggie search "${MOGGIE_SEARCH:-in:inbox}" |sed -e 's/ /\t/'  >"$MOGGIE_TMP"
    if [ ! -s "$MOGGIE_TMP" ]; then
        moggie_sh_info '*** No messages found, search again!'
    else
        MOGGIE_CHOICE="$($MOGGIE_SH_MAILLIST <$MOGGIE_TMP)"
        if [ $(echo "$MOGGIE_CHOICE" |wc -l) = 1 ]; then
            v
        else
            moggie_sh_done search
        fi
    fi
}

alias inbox='s in:inbox'
alias sent='s in:sent'
alias all-mail='s all:mail'

v() {
    IDS=$(echo $(echo "$MOGGIE_CHOICE" |cut -f1) |sed -e s'/ id:/ +id:/g' -e s'/ thread:/ +thread:/g')
    moggie show "$MOGGIE_SEARCH" "$IDS" |$MOGGIE_SH_VIEWER
    moggie_sh_done view
}

drafts() {
    mkdir -p "$MOGGIE_SH_HOMEDIR/Drafts"
    COUNT=$(echo $(ls -1 |wc -l))
    pushd "$MOGGIE_SH_HOMEDIR/Drafts" >/dev/null
    moggie_sh_info "*** $(pwd) has" $COUNT "drafts"
    if [ $COUNT -gt 0 ]; then
        ls -1
    fi
}



##[ Main ]####################################################################

mkdir -p $MOGGIE_SH_HOMEDIR
pushd $MOGGIE_SH_HOMEDIR >/dev/null

cat <<tac
                                        _
                                        \\\`*-.
    Welcome to moggie.sh!                )  _\`-.
                                        .  : \`. .
  A moggie-based e-mail client          : _   '  \\
  implemented as a set of bash          ; *\` _.   \`*-._
  functions.                            \`-.-'          \`-.
                                          ;       \`       \`.
                                          :.       .        \\
                                          . \\  .   :   .-'   .
                                          '  \`+.;  ;  '      :
                                          :  '  |    ;       ;-.
                                          ; '   : :\`-:     _.\`* ;
                                        .*' /  .*' ; .*\`- +'  \`*'
                                        \`*-*   \`*-*  \`*-*'
tac
moggie_sh_done
