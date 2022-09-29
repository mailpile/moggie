#!/bin/bash
#
# Launch and run `docker-internal.sh` in a pristine test environment.
# See comments in the internal script for details.
#
WORKDIR=$(cd $(dirname $0)/.. && pwd)
docker build -t moggie-test -f $WORKDIR/tools/Dockerfile_tests .
docker run --rm --volume $WORKDIR:/mnt/code moggie-test
