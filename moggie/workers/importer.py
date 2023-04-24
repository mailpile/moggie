# FIXME:
#
#  - Make explicit on the search engine side when wordblobs get updated,
#    instead of implictly on del_results.
#  - Add some flags/args for skipping filters etc. This will have to be
#    implemented using/consulting tags in addition to in:incoming.
#  - Make it possible to set a cap on memory usage; we get more efficient
#    (faster) imports by batching lots of mail together, but there need to
#    be limits here when running on smaller devices. It appears using a
#    BATCH_SIZE_FULL of 50k messages results in about half a GIG of RAM
#    getting consumed. What happens if we check resources.getrusage()?
#    And os.sysconf?  Or use psutil?
#
# https://stackoverflow.com/questions/938733/total-memory-used-by-python-process
# https://stackoverflow.com/questions/22102999/get-total-physical-memory-in-python
#
import logging
import os
import time
import traceback
import threading

from ..jmap.requests import *
from ..util.dumbcode import dumb_encode_asc, dumb_decode
from ..storage.files import FileStorage
from ..search.extractor import KeywordExtractor
from ..search.filters import FilterEngine, FilterError
from .base import BaseWorker


class ImportWorker(BaseWorker):
    """
    """
    KIND = 'import'
    NICE = 20
    BACKGROUND_TASK_SLEEP = 0

    BATCH_SIZE = 500
    BATCH_SIZE_FULL = 100000

    TICK_T = 300
    IDLE_T = 15

    def __init__(self, status_dir,
            app_worker=None,
            fs_worker=None,
            search_worker=None,
            metadata_worker=None,
            notify=None,
            name=KIND,
            log_level=logging.ERROR):

        BaseWorker.__init__(self, status_dir,
            name=name, notify=notify, log_level=log_level)
        self.functions.update({
            b'import_search': (True, self.api_import_search)})

        self.filters = FilterEngine().load(
            os.path.normpath(os.path.join(status_dir, '..', 'filters')),
            quick=False, create=True)

        self.fs = fs_worker
        self.app = app_worker
        self.search = search_worker
        self.metadata = metadata_worker
        self.imported = 0
        self.progress = None
        self.idle_running = False

        self.kwe = KeywordExtractor()  # FIXME: Configurable? Plugins?

        assert(self.app and self.search)

    def _ns_scope(self, tag_namespace, tags, _all=True):
        if not tag_namespace:
            return tags
        nt = ['@%s' % tag_namespace] if (_all and tag_namespace) else []
        if tags:
            if tag_namespace:
                nt.extend(
                    t if ('@' in t) else ('%s@%s' % (t, tag_namespace))
                    for t in tags)
            else:
                nt.extend(tags)
        return nt

    def on_tick(self):
        if not self.idle_running and self.progress is None:
            self.progress = self._no_progress(None)

    def on_idle(self):
        if not self.idle_running and self.progress is not None:
            self.idle_running = True
            logging.info('Launching full import in background.')
            def _full_index():
                try:
                    for tag_ns in [None]:  # FIXME
                        self._index_full_messages(None, tag_ns, self.progress)
                except Exception as e:
                    logging.exception('Indexing failed: %s' % e)
                finally:
                    self.progress = None
                    self.idle_running = False
            self.add_background_job(_full_index, which='full')

    def import_search(self,
            request_obj, initial_tags,
            tag_namespace=None, force=False, full=False):
        return self.call('import_search',
            request_obj, initial_tags, tag_namespace, bool(force), full)

    def api_import_search(self,
            request, initial_tags, tag_namespace, force, full, **kwargs):
        request_obj = to_jmap_request(request)
        caller = self._caller
        def background_import_search():
            rv = self._import_search(
                request_obj, initial_tags, tag_namespace, force, full,
                caller=caller)
        self.add_background_job(background_import_search)
        self.reply_json({'running': True})

    def _get_email(self, metadata):
        try:
            if metadata.pointers[0].is_local_file:
                return self.fs.email(metadata, text=True, data=False)
            else:
                return self.app.jmap(True,
                    RequestEmail(metadata=metadata, text=True),
                    ).get('email')
        except Exception as e:
            logging.debug('Failed to load %s: %s' % (metadata, e))
            return None

    def _notify_progress(self, progress):
        add = progress['emails_new']
        pct = progress['pct']
        if pct:
            msg = ('[import] %d new emails: %s' % (add, pct))
        else:
            total = progress['emails']
            done = (progress['pending'] == 0)
            upd = progress['emails_upd']
            old = total - add - upd
            msg = ('[import] %d new emails, updating %d, %d unchanged.%s'
                % (add, upd, old, ' Done!' if done else '..'))
        self.notify(msg, data=progress, caller=progress['caller'])

    def _index_full_messages(self, email_idxs, tag_namespace, progress):
        incoming = self._ns_scope(tag_namespace, ['in:incoming'], _all=False)
        incoming = incoming[0]
        if email_idxs:
            email_idxs = list(self.search.intersect(incoming, email_idxs))
        else:
            all_incoming = self.search.search(incoming)['hits']
            email_idxs = list(dumb_decode(all_incoming))
        if not email_idxs:
            logging.debug('No messages need processing, aborting.')
            return

        progress['emails_new'] = len(email_idxs)
        email_idxs = email_idxs[:self.BATCH_SIZE_FULL]
        progress['pending'] += 1
        keywords = {}
        ntime, bc, ec = int(time.time()), 0, 0
        for i in range(0, len(email_idxs), self.BATCH_SIZE):
            idx_batch = email_idxs[i:i+self.BATCH_SIZE]
            logging.debug('Processing messages %s' % (idx_batch))
            for md in self.metadata.metadata(idx_batch)['metadata']:
                # 1. Submit a request to the main app to fetch the e-mail's
                #    text parts and structure (not full attachments). Again,
                #    we don't know or care where the mail is coming from.
                email = self._get_email(md)
                if not email:
                    continue

                # 2. Generate keywords and tags
                stat, kws = self.kwe.extract_email_keywords(md, email)
                # FIXME: Check status: want more data? e.g. full attachments?

                # 3. Run the filtering logic to mutate keywords/tags
                if 0 == (self.imported % 1000):
                    self.filters.load()
                self.filters.filter(tag_namespace, kws, md, email)
                for kw in kws:
                    if kw in keywords:
                        keywords[kw].append(md.idx)
                    else:
                        keywords[kw] = [md.idx]

                bc += 1
                ec += 1
                if bc >= 113:
                    progress['pct'] = ('reading %d%%, %d/%d' % (
                        (100 * ec) // len(email_idxs), ec, len(email_idxs)))
                    bc = 0
                    if int(time.time()) > ntime:
                        ntime = int(time.time())
                        self._notify_progress(progress)

                self.imported += 1
                if not self.keep_running:
                    return

        # 4. Add/remove results from the search engine
        added, batch, bc = set(), [], 0
        for what, prefix in (
                ('tags',     'in:'),
                ('keywords', '')):
            kc = 0
            kw_batch = [k for k in keywords if k.startswith(prefix)]
            for kw in sorted(kw_batch):
                idxs = keywords[kw]
                kc += 1
                bc += len(idxs)
                added |= set(idxs)
                batch.append([idxs, kw])
                del keywords[kw]
                if bc >= 1000:
                    progress['pct'] = ('%s %d%%, %d/%d' % (
                        what, (100 * kc) // len(kw_batch), kc, len(kw_batch)))
                    self.search.add_results(batch, wait=True)
                    batch, bc = [], 0
                    if int(time.time()) > ntime:
                        ntime = int(time.time())
                        self._notify_progress(progress)
        if batch:
            self.search.add_results(batch, wait=True)
            progress['pct'] = ''

        # 5. Remove messages from Incoming
        self.search.del_results([[list(added), incoming]], wait=False)

        # 6. Report progress
        progress['pending'] -= 1
        self._notify_progress(progress)

    def _no_progress(self, caller):
        return {
            'caller': caller,
            'emails': 0,
            'emails_new': 0,
            'emails_upd': 0,
            'pct': '',
            'pending': 0}

    def _import_search(self,
            request_obj, initial_tags, tag_namespace, force, full,
            caller=None):

        if self.progress:
            progress = self.progress
        else:
            progress = self._no_progress(caller)
        progress['pending'] += 1

        def _full_indexer(email_idxs):
            def _full_index():
                self._index_full_messages(email_idxs, tag_namespace, progress)
            return _full_index

        tags = self._ns_scope(tag_namespace, ['in:incoming'] + initial_tags)
        done = False
        email_c = 0
        self.filters.load()
        while self.keep_running and not done:
            # 1. Submit a limited request_obj to the main app worker
            #    (The app is responsible for selecting the right backend
            #    mail source to process the request, we don't need to know
            #    where things are coming from)
            response = self.app.jmap(True, request_obj.update({
                'skip': email_c,
                'limit': self.BATCH_SIZE}))
            emails = response['emails'] or []
            email_c += len(emails)
            progress['emails'] += len(emails)
            done = (len(emails) < self.BATCH_SIZE)

            # 2. Add messages to metadata index, forward any new ones to the
            #    search engine for initial tagging (in:incoming, namespaces).
            idx_ids = self.metadata.add_metadata(emails, update=True)
            new_msgs = idx_ids['added']
            progress['emails_new'] += len(new_msgs)
            if force:
                new_msgs.extend(idx_ids['updated'])
                progress['emails_upd'] += len(idx_ids['updated'])
            if new_msgs:
                added = self.search.add_results([[new_msgs, tags]])

                # 3. When search engine reports success, schedule full indexing
                #    and filtering of that batch of messages. We could do all
                #    at once, but this way we can report progress.
                if full:
                    progress['pending'] += 1
                    self.add_background_job(
                        _full_indexer(new_msgs), which='full')

            # 4. Repeat until all mail is processed, report progress
            self._notify_progress(progress)

        # FIXME: error handling? ... what does that look like? Ugh.

        if not full and not self.progress:
            self.progress = progress

        progress['pending'] -= 1
        self._notify_progress(progress)
        return progress


if __name__ == '__main__':
    import sys
    from ..jmap.requests import RequestMailbox
    from ..jmap.responses import ResponseMailbox
    from ..email.metadata import Metadata

    logging.basicConfig(level=logging.DEBUG)

    class MockAppWorker:
        def jmap(self, access, request_obj):
            print('jmap: %s' % request_obj)
            return ResponseMailbox(request_obj, [
                Metadata.ghost('<ghost1@moggie>')
                ], False)

    class MockSearchWorker:
        def add_results(self, request_obj):
            print('add_results: %s' % request_obj)
            return {}

    iw = ImportWorker('/tmp',
            app_worker=MockAppWorker(),
            search_worker=MockSearchWorker(),
            name='moggie-imp-test').connect()
    if iw:
        print('URL: %s' % iw.url)
        try:
            iw.import_search(RequestMailbox(
                mailbox='/home/bre/Mail/klaki/2021-10.mbx'),
                ['in:fairyland'])
            time.sleep(0.6)

            if 'wait' in sys.argv[1:]:
                print('** Tests passed, waiting... **')
            else:
                iw.quit()
                print('** Tests passed, exiting... **')
            iw.join()
        finally:
            iw.terminate()

