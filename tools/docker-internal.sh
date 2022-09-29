#!/bin/bash
#
# This script runs inside a Docker container that has Moggie's reuirements
# installed, the git workspace mounted at /mnt/code, and some test e-mails
# downloaded to /root/test.mbx.
#
# It currently tests the following things:
#
#    - Launching moggie as a background process
#    - Importing mail
#    - Creating a context with an isolating namespace
#    - Granting HTTP-based remote access
#    - Using lots/curl to fetch and count search results
#    - Verify that context namespacing and search scoping correctly
#      excludes results that should not be exposed.
#
export PATH=$PATH:/mnt/code/tools

#cd /root
#mkdir -p .local/share/Moggie/default
#cat <<tac >.local/share/Moggie/default/config.rc
#[App]
#port=32025
#tac

cd /mnt/code

python3 -m moggie start
python3 -m moggie import /root/test.mbx

python3 -m moggie context create Testspace --tag-namespace=Testspace
python3 -m moggie grant create Tester user --context=Testspace
python3 -m moggie grant login Tester --output=urls
TESTER_URL=$(python3 -m moggie grant list Tester --output=urls |grep http: |cut -b40-)

while [ $(lots count in:incoming) -gt 0 ]; do
    echo "Waiting for importer... $(lots count in:incoming) incoming still"
    sleep 1
done
echo
lots search gitbox
echo

TOTAL=$(lots count gitbox)
[ $TOTAL -gt 0 ] && echo -ne 'OK\t' || echo -n 'FAIL\t'
echo "Have $TOTAL hits for gitbox"

PRETAG=$(curl -s "$TESTER_URL/cli/count/gitbox?format=text")
[ $PRETAG = 0 ] && echo -ne 'OK\t' || echo -n 'FAIL\t'
echo "Tester sees $PRETAG hits for gitbox, before tagging."

lots tag +in:@testspace -- gitbox >/dev/null \
    && echo -ne 'OK\t' || echo -n 'FAIL\t'
echo "Tagged gitbox hits into namespace 'testspace'"

POSTTAG=$(curl -s "$TESTER_URL/cli/count/gitbox?format=text")
[ $POSTTAG = $TOTAL ] && echo -ne 'OK\t' || echo -n 'FAIL\t'
echo "Tester sees $POSTTAG hits for gitbox, after tagging."

python3 -m moggie context update Testspace --forbid=superusers --output=scope >/dev/null \
    && echo -ne 'OK\t' || echo -n 'FAIL\t'
echo "Forbidding 'superusers' results in Testspace..."

POSTFB=$(curl -s "$TESTER_URL/cli/count/gitbox?format=text")
[ $POSTFB -lt $POSTTAG ] && echo -ne 'OK\t' || echo -n 'FAIL\t'
echo "Tester sees $POSTFB hits for gitbox, after forbidding."

echo
python3 -m moggie stop
