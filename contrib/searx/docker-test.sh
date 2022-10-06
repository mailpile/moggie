#!/bin/bash
#
# This script runs inside a Docker container that has Moggie's requirements
# installed, the git workspace mounted at /mnt/code, and some test e-mails
# downloaded to /root/test.mbx.
#
# It then installs Searx and the moggie searx integration, making it easy
# to experiment with both.
#
if [ "$1" != "--internal" ]; then
    WORKDIR=$(cd $(dirname $0)/../.. && pwd)
    docker build -t moggie-searx-test -f $WORKDIR/contrib/searx/Dockerfile_tests .
    exec docker run --rm --volume $WORKDIR:/mnt/code moggie-searx-test
fi

echo
echo "=== Running second stage inside Docker ================================="
echo

# Set PATH and figure out our IP address
export DOCKER_IP=$(ip addr |grep global |cut -f1 -d/|awk '{print $2}')
export PATH=$PATH:/mnt/code/tools
echo 'export PATH=$PATH:/mnt/code/tools' >/root/.bashrc
echo 'export PS1="moggie-searx-integration $ "' >>/root/.bashrc
set -x

# Make it easy to look at the logs, enable Moggie debug logs
cd /root
mkdir -p .local/share/Moggie/default/logs
ln -fs /root/.local/share/Moggie/default/logs logs
cat >/root/.local/share/Moggie/default/config.rc <<tac
[App]
port = 32025
log_level = 10
tac

# Launch Moggie, import some mail and create a testing context
cd /mnt/code
python3 -m moggie start
python3 -m moggie import /root/test.mbx
lots context create Testspace --tag-namespace=Testspace
lots grant create Tester user --context=Testspace
MOGGIE_URL=$(lots grant login Tester --output=urls |grep http: |cut -b40- |tail -1)


# Install Searx
cd /root
git clone https://github.com/BjarniRunar/searx-moggie-integration  # Shortcut
mv searx-moggie-integration searx
cp -v /mnt/code/contrib/searx/moggie.py searx/searx/engines/
cd /root/searx
python3 -m venv .env
. .env/bin/activate
pip install -U pip
pip install -U setuptools
pip install -U wheel
pip install -U pyyaml
pip install -e .


# Configure and launch Searx
export SEARX_SETTINGS_PATH="/root/searx-settings.yml"
cp utils/templates/etc/searx/use_default_settings.yml  $SEARX_SETTINGS_PATH
sed -i -e "s/ultrasecretkey/$(openssl rand -hex 16)/g" $SEARX_SETTINGS_PATH
sed -i -e "s/{instance_name}/searx@$(uname -n)/g"      $SEARX_SETTINGS_PATH
sed -i -e "s/debug : False/debug : True/g"             $SEARX_SETTINGS_PATH
sed -i -e "s/127.0.0.1/0.0.0.0/g"                      $SEARX_SETTINGS_PATH
sed -i -e "s/8888/32080/g"                             $SEARX_SETTINGS_PATH
sed -i -e "s,base_url.*YOUR_SECRET_TOKEN/',base_url : '$MOGGIE_URL/',g" \
    /root/searx/searx/settings.yml
python3 searx/webapp.py >/root/logs/searx.log 2>&1 &


# The import is probably done by now, tag messages into the Testspace
lots tag +@Testspace -- all:mail


set +x
(while true; do
    cat <<tac
=== Some hints: =======================================================

Use this command to kill the container:              (may require sudo)

  docker kill \$(docker ps |grep moggie-searx |cut -f1 -d\\ )

To enter the container interactively:

  docker exec -i \$(docker ps |grep moggie-searx |cut -f1 -d\\ ) bash -i

Searx is on: http://$DOCKER_IP:32080/

Searching in Searx for "gitbox" should give some Moggie results. You
will not be able to click the links (they are localhost links, within
the container).

=== Tailing logs... ===================================================
tac
    sleep 60
done) &

sleep 10
echo
exec tail -n 0 -f /root/logs/*
