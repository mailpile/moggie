#!/bin/bash
rm -rf tmp
mkdir -p new cur tmp

SOP=${SOP:-pgpy}

FN='cur/00000001.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Simple plain-text e-mail' \
    --message='This is a test, I hope you like it' \
    --html=N \
    --from='Alice Lovelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000002.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='UTF-8 plain-text e-mail' \
    --message='Thís ís a test, I hope you lææææk it' \
    --html=N \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lövelace <alice@openpgp.example>' \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000003.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Multipart/alternative e-mail (with HTML)' \
    --message='Thís ís a test, I hope you lææææk it' \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000004.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Multipart/mixed e-mail (HTML, attachments)' \
    --message='Thís ís a test, I hope you lææææk it' \
    --attach="$0" \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000005.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Simple signed e-mail' \
    --message='Thís ís a test, I hope you lææææk it' \
    --html=N \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000006.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Multipart signed e-mail with attachment' \
    --message='Thís ís a test, I hope you lææææk it' \
    --attach="$0" \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000007.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Simple encrypted e-mail' \
    --message='Thís ís a test, I hope you lææææk it' \
    --html=N \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    --encrypt=all \
    --encrypt-to=PGP:@CERT:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000008.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Multipart encrypted e-mail with attachment' \
    --message='Thís ís a test, I hope you lææææk it' \
    --attach="$0" \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    --encrypt=all \
    --encrypt-to=PGP:@CERT:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000009.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Simple signed e-mail with signed headers' \
    --message='Thís ís a test, I hope you lææææk it' \
    --html=N \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --pgp-headers=sign \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000010.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Simple encrypted e-mail with protected headers' \
    --message='Thís ís a test, I hope you lææææk it' \
    --html=N \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --cc='Alice Two <alice2@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --pgp-headers=all \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    --encrypt=all \
    --encrypt-to=PGP:@CERT:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"

FN='cur/00000011.mailpile:2,S'
[ -e "$FN" ] || moggie email --subject='Signed e-mail w/ Autocrypt' \
    --message='Thís ís a test, I hope you lææææk it' \
    --html=N \
    --from='Alice Lövelace <alice@openpgp.example>' \
    --to='Alice Lovelace <alice@openpgp.example>' \
    --pgp-key-sources=demo --pgp-sop=$SOP \
    --pgp-headers=sign \
    --sign-with=PGP:@PKEY:alice@openpgp.example \
    --autocrypt-with=mutual:alice@openpgp.example \
    2>>tmp/make-errors.log |sed -e 's/\r//' >$FN && ls -l "$FN"


