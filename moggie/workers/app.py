import os
import sys
import socket

from upagekite import uPageKite, LocalHTTPKite
from upagekite.httpd import HTTPD, url, async_url
from upagekite.proto import uPageKiteDefaults
from upagekite.web import process_post
from upagekite.websocket import websocket

from ..config import APPNAME as MAIN_APPNAME
from ..config import APPURL as MAIN_APPURL
from ..storage.metadata import MetadataStore
from ..util.rpc import JsonRpcClient
from .public import PublicWorker, require

#
# TODO: Define how we handle RPCs over the websocket. There needs to be some
#       structure there! Assume everything is always async.
#

from ..email.metadata import Metadata
std_tags = [[
        {'sc':'i', 'name': 'INBOX',    'count': 10},
        {'sc':'c', 'name': 'Calendar', 'count': 1},
        {'sc':'p', 'name': 'People',   'count': 2},
    ],[
        {'sc':'a', 'name': 'All Mail', 'count': 2},
        {'sc':'d', 'name': 'Drafts',   'count': 1},
        {'sc':'o', 'name': 'Outbox',   'count': 1},
        {'sc':'s', 'name': 'Sent',     'count': 3},
        {'sc':'j', 'name': 'Spam',     'count': 2},
        {'sc':'t', 'name': 'Trash',    'count': 1}]]
test_contexts = [{
        'name': 'Local mail',
        'emails': [],
        'tags': std_tags}]
unused = [{
        'name': 'Personal',
        'emails': ['bre@klaki.net', 'bjarni.runar@gmail.com'],
        'tags': std_tags
    },{
        'name': 'PageKite',
        'emails': ['bre@pagekite.net', 'ehf@beanstalks-project.net'],
        'tags': std_tags
    },{
        'name': 'PageKite Support',
        'emails': ['info@pagekite.net', 'help@pagekite.net'],
        'tags': std_tags
    },{
        'name': 'Mailpile',
        'emails': ['bre@mailpile.is'],
        'tags': std_tags}]
raw_msg = b'''\
Date: Wed, 1 Sep 2021 00:03:01 GMT
From: Bjarni <bre@example.org>
To: "Some One" <someone@example.org>
Subject: Hello world'''
test_emails = ([
    Metadata(0, b'/tmp/foo', 0, 0, 0, raw_msg).parsed()] * 10)


@async_url('/rpc')
@websocket('app')
async def web_websocket(opcode, msg, conn, ws,
                        first=False, eof=False, websocket=True):
    if not websocket:
        return {'code': 400, 'body': 'Sorry\n'}


@async_url('/')
async def web_root(req_env):
    require(req_env, secure=True)
    return {'code': 500, 'body': 'Sorry\n'}


@async_url('/', '/rpc/*')
async def web_rpc(req_env):
    require(req_env, post=True, secure=True)
    return {'code': 500, 'body': 'Sorry\n'}


@async_url('/recovery_svc/*')
@process_post(max_bytes=2048, _async=True)
async def proxy_recovery_svc(req_env):
    require(req_env, post=True, secure=True)
    posted = req_env.post_data
    print('FIXME: Proxy to recovery_svc worker' % posted)
    return {'code': 500, 'body': 'Proxy Not Ready\n'}


class AppWorker(PublicWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'app'
    PUBLIC_PATHS = ['/']
    PUBLIC_PREFIXES = ['/recovery_svc']

    def websocket_url(self):
        return 'ws' + self.url[4:] + '/rpc'

    def start_workers(self):
        pass  # FIXME

    def load_metadata(self):
        self.metadata = MetadataStore(
            os.path.join(self.profile_dir, 'metadata'), 'metadata',
            aes_key=b'bogus AES key')  # FIXME

    def startup_tasks(self):
        self.start_workers()
        self.load_metadata()


if __name__ == '__main__':
    aw = AppWorker('/tmp').connect()
    if aw:
        try:
            print('** We are live, yay')
            #print(aw.capabilities())
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
