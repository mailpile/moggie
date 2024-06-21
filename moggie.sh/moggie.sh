#!/bin/bash
#
exec bash --init-file <(sed -e '1,/^exec/ d' $0) -i

##[ Setup ]###################################################################

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

moggie_sh_done() {
    moggie_sh_divider
    case "$1" in
        download)
            echo "*** Downloaded to: $(dirname $(grep /message.txt $MOGGIE_TMP))"
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
        echo -e "${BBLUE}Commands: v=view, d=download, r=reply, f=forward, s=search, q=quit${RESET}"
    else
        echo -e "${BBLUE}Commands: c=compose, t=tags, s=search, q=quit${RESET}"
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

    # Make sure it exists, normalize variables
    mkdir -p "$MOGGIE_SH_DRAFT"
    chmod go-rwx "$MOGGIE_SH_DRAFT"
    cd "$MOGGIE_SH_DRAFT"
    MOGGIE_SH_DRAFT="$(pwd)"

    if [ ! -e "$MOGGIE_SH_DRAFT/message.txt" ]; then
        cat <<tac >"$MOGGIE_SH_DRAFT/message.txt"
To: You <you@example.org>
From: Me <me@example.org>
Subject: Draft e-mail

$(cat ~/.signature 2>/dev/null)

=============================================================[moggie-sh-snip]==
Type your message above this line!

If you want to add attachments, exit the editor and copy them to the draft's
folder: $MOGGIE_SH_DRAFT

Once you're happy, send the e-mail by typing \`send\` at the moggie.sh prompt.
tac
    fi

    ${VISUAL:-${EDITOR:-vi}} "message.txt"

    SUBJECT=$(grep ^Subject: "message.txt" \
        |head -1 \
        |cut -f2 -d:)
    if [ "$SUBJECT" != "" ]; then
        NN="$(dirname "$MOGGIE_SH_DRAFT")/$(date +%Y%m%d-%H%M) $SUBJECT"
        [ "$MOGGIE_SH_DRAFT" != "$NN" ] \
            && mv "$MOGGIE_SH_DRAFT" "$NN" \
            && MOGGIE_SH_DRAFT="$NN"
    fi
    cd "$MOGGIE_SH_DRAFT"
}

d() {
    IDS=$(echo $(echo "$MOGGIE_CHOICE" |cut -f1) |sed -e s'/ id:/ +id:/g')
    moggie search "$MOGGIE_SEARCH" "$IDS" --format=msgdirs |tar xvfz - >$MOGGIE_TMP
    moggie_sh_done download
}

alias q=exit

s() {
    MOGGIE_SEARCH="$@"
    moggie search "${MOGGIE_SEARCH:-in:inbox}" |sed -e 's/ /\t/'  >"$MOGGIE_TMP"
    if [ ! -s "$MOGGIE_TMP" ]; then
        echo '*** No messages found, search again!'
    else
        MOGGIE_CHOICE="$($MOGGIE_SH_MAILLIST <$MOGGIE_TMP)"
        if [ $(echo "$MOGGIE_CHOICE" |wc -l) = 1 ]; then
            v
        else
            moggie_sh_done search
        fi
    fi
}

v() {
    IDS=$(echo $(echo "$MOGGIE_CHOICE" |cut -f1) |sed -e s'/ id:/ +id:/g')
    moggie show "$MOGGIE_SEARCH" "$IDS" |$MOGGIE_SH_VIEWER
    moggie_sh_done view
}

drafts() {
    mkdir -p "$MOGGIE_SH_HOMEDIR/Drafts"
    pushd "$MOGGIE_SH_HOMEDIR/Drafts" >/dev/null
    echo "*** $(pwd) has" $(ls -1 |wc -l) "drafts"
}



##[ Main ]####################################################################

mkdir -p $MOGGIE_SH_HOMEDIR
cd $MOGGIE_SH_HOMEDIR

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
