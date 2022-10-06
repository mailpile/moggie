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
if [ "$1" != "--internal" ]; then
    WORKDIR=$(cd $(dirname $0)/.. && pwd)
    docker build -t moggie-test -f "$WORKDIR/tools/Dockerfile_tests" .
    if [ "$1" == '--wait' ]; then
        touch "$WORKDIR/.docker-test-wait"
    else
        rm -f "$WORKDIR/.docker-test-wait"
    fi
    exec docker run --rm --volume "$WORKDIR:/mnt/code" moggie-test
fi

##############################################################################
echo
echo '=== Resuming docker-test.sh inside docker environment ================='
echo

export PATH=$PATH:/mnt/code/tools
# Set PATH and figure out our IP address
export DOCKER_IP=$(ip addr |grep global |cut -f1 -d/|awk '{print $2}')
export PATH=$PATH:/mnt/code/tools
export PYTHONPATH=/mnt/code
cat >/root/.bashrc <<tac
export PATH=\$PATH:/mnt/code/tools
export PYTHONPATH=/mnt/code
export PS1="moggie-test $ "
tac
set -x

# Make it easy to look at the logs, enable Moggie debug logs
cd /root
mkdir -p .local/share/Moggie/default/logs
cat >/root/.local/share/Moggie/default/config.rc <<tac
[App]
port = 32025
log_level = 10
tac
ln -fs /root/.local/share/Moggie/default moggie
ln -fs /root/.local/share/Moggie/default/logs moggie-logs
ln -fs /root/.local/share/Moggie/default/config.rc moggie-config.rc


python3 -m moggie start
python3 -m moggie import /root/test.mbx

lots context create Testspace --tag-namespace=Testspace
lots grant create Tester user --context=Testspace
TESTER_URL=$(python3 -m moggie grant login Tester --output=urls |grep http: |cut -b40- |tail -1)

set +x
while [ $(lots count in:incoming) -gt 0 ]; do
    echo "Waiting for importer... $(lots count in:incoming) incoming still"
    sleep 1
done
echo
lots search gitbox

echo
echo '=== Running tests ====================================================='
echo

TOTAL=$(lots count gitbox)
[ $TOTAL -gt 0 ] && echo -ne 'OK\t' || echo -ne 'FAIL\t'
echo "Have $TOTAL hits for gitbox"

PRETAG=$(curl -s "$TESTER_URL/cli/count/gitbox?format=text")
[ $PRETAG = 0 ] && echo -ne 'OK\t' || echo -ne 'FAIL\t'
echo "Tester sees $PRETAG hits for gitbox, before tagging."

lots tag +in:@testspace -- gitbox >/dev/null \
    && echo -ne 'OK\t' || echo -ne 'FAIL\t'
echo "Tagged gitbox hits into namespace 'testspace'"

POSTTAG=$(curl -s "$TESTER_URL/cli/count/gitbox?format=text")
[ $POSTTAG = $TOTAL ] && echo -ne 'OK\t' || echo -ne 'FAIL\t'
echo "Tester sees $POSTTAG hits for gitbox, after tagging."

python3 -m moggie context update Testspace --forbid=superusers --output=scope >/dev/null \
    && echo -ne 'OK\t' || echo -ne 'FAIL\t'
echo "Forbidding 'superusers' results in Testspace..."

POSTFB=$(curl -s "$TESTER_URL/cli/count/gitbox?format=text")
[ $POSTFB -lt $POSTTAG ] && echo -ne 'OK\t' || echo -ne 'FAIL\t'
echo "Tester sees $POSTFB hits for gitbox, after forbidding."

if [ -e /mnt/code/.docker-test-wait ]; then
    cat <<tac

=== Waiting! ==========================================================

To shutdown:
  docker kill \$(docker ps |grep moggie-test |cut -f1 -d\\ )

To launch a shell:
  docker exec -i \$(docker ps |grep moggie-test |cut -f1 -d\\ ) bash -i

This container will self destruct in one hour.
tac
    sleep 3600
else
    python3 -m moggie stop
fi
