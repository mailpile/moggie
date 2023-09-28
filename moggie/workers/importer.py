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

from ..api.requests import *
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

    BATCH_SIZE = 5000
    BATCH_SIZE_FULL = 50000

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
        self.progress = self._no_progress(None)
        self.idle_running = False

        self.kwe = KeywordExtractor()  # FIXME: Configurable? Plugins?

        self.lock = threading.Lock()
        self.keyword_batches = []
        self.keyword_batch_no = 0
        self.keyword_thread = None
        self.keywords = {}

        assert(self.app and self.search)

    def run(self, *args, **kwargs):
        if hasattr(self.fs, 'forked'):
            self.fs.forked()
        return super().run(*args, **kwargs)

    def _fix_tags_and_scope(self, tag_namespace, tags, _all=True):
        def _fix(tag):
            tag = tag.lower()
            if tag[:3] == 'in:':
                return tag
            elif tag[:4] == 'tag:':
                return 'in:%s' % tag[4:]
            else:
                return 'in:%s' % tag

        tags = set(_fix(t) for t in tags)
        if not tag_namespace:
            return sorted(list(tags))

        nt = ['@%s' % tag_namespace] if (_all and tag_namespace) else []
        nt.extend(
            t if ('@' in t or t in special) else ('%s@%s' % (t, tag_namespace))
            for t in tags)

        return sorted(list(set(nt)))

    def on_tick(self):
        if not self.idle_running and self.progress is None:
            self.progress = self._no_progress(None)

    def on_idle(self, last_running):
        if not self.idle_running and self.progress is not None:
            self.idle_running = True
            logging.info('Launching full import in background.')
            def _full_index():
                self._start_keyword_loop()
                if self.keyword_batches:
                    return
                try:
                    for tag_ns in [None]:  # FIXME
                        self._index_full_messages(None, tag_ns, self.progress)
                except Exception as e:
                    logging.exception('Indexing failed: %s' % e)
                finally:
                    self.idle_running = False
            self.add_background_job(_full_index, which='full')

    def import_search(self,
            request_obj, initial_tags,
            tag_namespace=None, force=False, full=False, compact=False):
        return self.call('import_search',
            request_obj, initial_tags, tag_namespace,
            bool(force), full, compact)

    def api_import_search(self,
            request, initial_tags, tag_namespace, force, full, compact,
            **kwargs):
        request_obj = to_api_request(request)
        caller = self._caller
        def background_import_search():
            try:
                rv = self._import_search(
                    request_obj, initial_tags, tag_namespace, force, full,
                    compact, caller=caller)
            except:
                import traceback
                traceback.print_exc()
        self.add_background_job(background_import_search)
        self.reply_json({'running': True})

    def _get_email(self, metadata):
        try:
            if metadata.pointers[0].is_local_file:
                return self.fs.email(metadata, text=True, data=False)
            else:
                return self.app.api_request(True,
                    RequestEmail(metadata=metadata, text=True),
                    ).get('email')
        except Exception as e:
            logging.exception('Failed to load %s: %s' % (metadata, e))
            return None

    def _notify_progress(self, progress=None):
        progress = progress or self.progress
        add = progress['emails_new']
        pct = progress['pct']
        kw = progress['kw']
        if pct or kw:
            msg = ('[import] %d new emails: %s' % (add, pct or kw))
        else:
            total = progress['emails']
            done = (progress['pending'] == 0)
            upd = progress['emails_upd']
            old = total - add - upd
            msg = ('[import] %d new emails, updating %d, %d unchanged.%s'
                % (add, upd, old, ' Done!' if done else '..'))
        self.notify(msg, data=progress, caller=progress['caller'])

    def _keyword_loop(self, delay=None):
        """
        This loop runs in its own thread, and may be launched by any
        import process but may span multiple imports depending on how
        things line up.

        It spends most of its time blocking on self.search.add_imports,
        so Python threading actually helps here. The loop will repeatedly
        try and flush everything in self.keywords to the index, but on
        each iteration it starts off with infrequently found keywords,
        saving the common ones for last.

        Since this thread generally runs in parallel to the thread which
        reads mail, updating the common keywords last gives the reader as
        much time as possible to find all the matches; thus reducing how
        minimizing how often we update the same keyword when importing a
        given batch of mail, this should save time and cycles overall.

        The mail readers take a note of which "batch" is currently being
        processed by the loop, and use that as a marker to tell this
        loop when to mark a set of messages as fully processed (by
        untagging in:incoming or in:incoming-old).
        """
        if delay:
            time.sleep(delay)
        ntime = int(time.time())
        while self.keep_running:
            self.progress['kw'] = ''
            time.sleep(0.25)
            if not self.keywords and not self.keyword_batches:
                logging.debug('keyword loop: exiting')
                return

            logging.debug(
                'keyword loop: Entered loop (keywords=%d)'
                % len(self.keywords))

            # Add/remove results from the search engine
            for what, touch, prefix in (
                    ('tags', True, 'in:'), ('rare keywords', False, ''),
                    ('tags', True, 'in:'), ('rare keywords', False, ''),
                    ('tags', True, 'in:'), ('rare keywords', False, ''),
                    ('tags', True, 'in:'), ('rare keywords', False, ''),
                    ('common keywords', False, '')):
                with self.lock:
                    keywords = self.keywords
                    batch = [k for k in keywords if k.startswith(prefix)]

                    if what == 'tags':
                        pass
                    elif what == 'common keywords':
                        self.keyword_batch_no += 1
                    else:
                        batch = [k for k in batch if len(keywords[k]) < 3]

                pairs, pc, kc = [], 0, 0
                for i, kw in enumerate(sorted(batch)):
                    last_kw = (i == (len(batch)-1))

                    with self.lock:
                        idxs = keywords[kw]
                        pairs.append([idxs, kw])
                        del keywords[kw]

                    pc += len(idxs)
                    kc += 1

                    if last_kw or (pc >= 25000) or (len(pairs) >= 150):
                        self.progress['kw'] = ('%s %d%%, %d/%d' % (
                            what,
                            (100 * kc) // len(batch),
                            kc, len(batch)))

                        self.search.add_results(pairs, wait=True, touch=touch)
                        pairs, pc = [], 0

                        if int(time.time()) > ntime:
                            ntime = int(time.time())
                            self._notify_progress()
                        else:
                            time.sleep(0.02)
                        if not self.keep_running:
                            logging.debug('keyword loop: exiting early')
                            return

            self.progress['kw'] = ''
            with self.lock:
                if self.keywords:
                    done_no = self.keyword_batch_no
                    _all = self.keyword_batches
                    done = [batch for batch in _all if batch[0] < done_no]
                    keep = [batch for batch in _all if batch[0] >= done_no]
                    self.keyword_batches = keep
                else:
                    done, self.keyword_batches = self.keyword_batches, []

            # 5. Remove messages from Incoming
            for bno, tag, idxs in done:
                self.progress['pending'] -= 1
                logging.debug('Marking batch %d complete (%s): %s messages'
                    % (bno, tag, len(idxs)))
                self.search.del_results([[idxs, tag]], wait=True)

            # 6. Report progress
            self._notify_progress(self.progress)

    def _start_keyword_loop(self, after=None):
        with self.lock:
            if self.keyword_thread and self.keyword_thread.is_alive():
                pass
            else:
                logging.debug('keyword loop: Launching thread')
                def _loop_runner():
                    try:
                        self._keyword_loop(delay=after)
                    except:
                        logging.exception('Keyword loop crashed')
                    self.keyword_thread = None
                thr = threading.Thread(target=_loop_runner)
                self.keywords = self.keywords or {}
                self.keyword_thread = thr
                self.keyword_thread.daemon = True
                self.keyword_thread.start()
        return self.keywords

    def _index_full_messages2(self, email_idxs, tag_namespace, progress, old):
        in_queue = 'in:incoming-old' if old else 'in:incoming'
        incoming = self._fix_tags_and_scope(tag_namespace, [in_queue], _all=False)
        incoming = incoming[0]
        if email_idxs:
            email_idxs = list(self.search.intersect(incoming, email_idxs))
        else:
            all_incoming = self.search.search(incoming)['hits']
            email_idxs = list(dumb_decode(all_incoming))
        if not email_idxs:
            logging.debug('No messages to process (%s).' % incoming)
            return

        progress['emails_new'] = len(email_idxs)
        email_idxs = email_idxs[:self.BATCH_SIZE_FULL]
        progress['pending'] += 1

        ntime, bc, ec = int(time.time()), 0, 0

        message_batches = []
        for i in range(0, len(email_idxs), self.BATCH_SIZE):
            last_loop = len(email_idxs) <= i+self.BATCH_SIZE

            idx_batch = email_idxs[i:i+self.BATCH_SIZE]
            logging.info(
                'Processing [%d..%d]/%d (last_loop=%s, keywords=%d)' % (
                    i, i+len(idx_batch)-1, len(email_idxs), last_loop,
                    len(self.keywords)))

            # 1. Submit a request to the main app to fetch the e-mail's
            #    text parts and structure (not full attachments). Again,
            #    we don't know or care where the mail is coming from.
            all_metadata = list(self.metadata.metadata(idx_batch)['metadata'])

            # Start processing keywords in parallel after 1s (or less).
            self._start_keyword_loop(after=min(1, len(all_metadata) / 100))
            added = []
            for md in all_metadata:
                if md.more.get('missing'):
                    continue

                email = self._get_email(md)
                if not email:
                    logging.exception('Failed to load %s' % (md,))
                    continue

                # 2. Generate keywords and tags
                stat, kws = self.kwe.extract_email_keywords(md, email)
                # FIXME: Check status: want more data? e.g. full attachments?

                # 3. Run the filtering logic to mutate keywords/tags
                if not old:
                    if 0 == (self.imported % 1000):
                        self.filters.load()
                    self.filters.filter(tag_namespace, kws, md, email)
                with self.lock:
                    for kw in kws:
                        if kw in self.keywords:
                            self.keywords[kw].append(md.idx)
                        else:
                            self.keywords[kw] = [md.idx]

                bc += 1
                ec += 1
                if bc >= 113:
                    progress['pct'] = ('reading %d%%, %d/%d' % (
                        (100 * ec) // len(email_idxs), ec, len(email_idxs)))
                    bc = 0
                    if int(time.time()) > ntime:
                        ntime = int(time.time())
                        self._notify_progress(progress)

                added.append(md.idx)
                self.imported += 1
                if not self.keep_running:
                    return

            # This marks our set of messages as complete, after the *next*
            # batch of keywords is uploaded to the index.
            with self.lock:
                self.keyword_batches.append(
                    (self.keyword_batch_no, incoming, added))

        progress['pct'] = ''
        for i in range(0, 4 * 300):
            if ((not self.keyword_batches and len(self.keywords) < 50000)
                    or not self.keep_running):
                break
            self._start_keyword_loop()
            time.sleep(0.25)
        self._notify_progress(progress)

    def _index_full_messages(self, email_idxs, tag_namespace, progress):
        self._index_full_messages2(email_idxs, tag_namespace, progress, False)
        self._index_full_messages2(email_idxs, tag_namespace, progress, True)

    def _no_progress(self, caller):
        return {
            'caller': caller,
            'emails': 0,
            'emails_new': 0,
            'emails_upd': 0,
            'pct': '',
            'kw': '',
            'pending': 0}

    def _import_search(self,
            request_obj, initial_tags, tag_namespace, force, full, compact,
            caller=None):

        if self.progress:
            progress = self.progress
        else:
            progress = self._no_progress(caller)
        progress['pending'] += 1

        def _full_indexer(email_idxs):
            def _full_index():
                self._index_full_messages(email_idxs, tag_namespace, progress)
                if compact:
                    logging.info('Compacting metadata')
                    self.metadata.compact(full=True)
                    logging.info('Compacting search index')
                    self.search.compact(full=True)
            return _full_index

        work_queue = 'in:incoming-old'
        for magic in ('incoming', 'in:incoming', 'inbox', 'in:inbox'):
            if magic in initial_tags:
                work_queue = 'in:incoming'
                initial_tags.remove(magic)

        tags = self._fix_tags_and_scope(tag_namespace, [work_queue] + initial_tags)
        done = False
        email_c = 0
        self.filters.load()
        while self.keep_running and not done:
            # 1. Submit a limited request_obj to the main app worker
            #    (The app is responsible for selecting the right backend
            #    mail source to process the request, we don't need to know
            #    where things are coming from)
            response = self.app.api_request(True, request_obj.update({
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

                # 3. When search engine reports success, schedule full
                #    indexing and filtering of that batch of messages. We
                # could do all at once, but this way we can report progress.
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
    import sys, traceback
    from ..api.requests import RequestMailbox
    from ..api.responses import ResponseMailbox
    from ..email.metadata import Metadata

    logging.basicConfig(level=logging.DEBUG)
    logging.info = print
    logging.error = print
    logging.debug = print
    logging.exception = lambda t: print('%s\n%s' % (t, traceback.format_exc()))

    class MockAppWorker:
        def api_request(self, access, request_obj):
            print('api_request: %s' % request_obj)
            return ResponseMailbox(request_obj,
                [Metadata.ghost('<ghost1@moggie>')], False)

    class MockMetadataWorker:
        def __init__(self):
            self.metadata = []
        def add_metadata(self, adding, update=True):
            print('add_metadata(%s)' % adding)
            self.metadata = adding
            return {'added': list(range(0, len(adding)))}

    class MockSearchWorker:
        def add_results(self, request_obj):
            print('add_results: %s' % request_obj)
            return {}

    os.system('mkdir -p /tmp/moggie/workers')
    iw = ImportWorker('/tmp/moggie/workers',
            app_worker=MockAppWorker(),
            metadata_worker=MockMetadataWorker(),
            search_worker=MockSearchWorker(),
            name='moggie-imp-test').connect()
    if iw:
        print('ImportWorker URL: %s' % iw.url)
        try:
            print('import_search -> %s' % (iw.import_search(RequestMailbox(
                mailbox='/home/bre/Mail/klaki/2021-10.mbx'),
                ['in:fairyland', 'in:inbox']),))
            time.sleep(5)

            if 'wait' in sys.argv[1:]:
                print('** Tests passed, waiting... **')
            else:
                iw.quit()
                print('** Tests passed, exiting... **')
            iw.join()
        finally:
            iw.terminate()

