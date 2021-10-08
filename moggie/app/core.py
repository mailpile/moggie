import os
import time

from ..storage.files import FileStorage
from ..storage.metadata import MetadataStore
from ..util.rpc import JsonRpcClient
from ..workers.storage import StorageWorker

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




class AppCore:
    def __init__(self, app_worker):
        self.worker = app_worker
        self.config = None
        self.metadata = None

    # Lifecycle

    def start_workers(self):
        self.email_accounts = {}
        self.storage = StorageWorker(self.worker.worker_dir,
            FileStorage(relative_to=os.path.expanduser('~')),
            name='fs').connect()

    def stop_workers(self):
        # The order here may matter
        all_workers = (self.storage,)
        for p in (1, 2, 3):
            for worker in all_workers:
                try:
                    if p == 1:
                        worker.quit()
                    if p == 2 and worker.is_alive():
                        worker.join(1)
                    if p == 3 and worker.is_alive():
                        worker.terminate()
                except:
                    pass
            time.sleep(0.1)

    def load_metadata(self):
        self.metadata = MetadataStore(
            os.path.join(self.worker.profile_dir, 'metadata'), 'metadata',
            aes_key=b'bogus AES key')  # FIXME

    def startup_tasks(self):
        self.start_workers()
        self.load_metadata()

    def shutdown_tasks(self):
        self.stop_workers()


    # Public API

    async def api_jmap(self, request_user, jmap_request):
        print('Request: %s' % (jmap_request,))
        return {
            'code': 200,
            'mimetype': 'application/json',
            'body': 'Nice'}

