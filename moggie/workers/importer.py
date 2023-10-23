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
import asyncio
import base64
import logging
import os
import random
import time
import traceback
import threading

from .base import BaseWorker
from ..api.requests import *
from ..util.dumbcode import dumb_encode_asc, dumb_decode
from ..util.intset import IntSet
from ..storage.files import FileStorage
from ..search.extractor import KeywordExtractor
from ..search.filters import FilterEngine, FilterError
from ..app.cli.email import CommandParse


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
            b'autotag': (True, self.api_autotag),
            b'autotag_train': (True, self.api_autotag_train),
            b'autotag_classify': (True, self.api_autotag_classify),
            b'import_search': (True, self.api_import_search)})

        self.filters = FilterEngine()  # FIXME: add moggie, encryption keys?
        self.filters.load(
            os.path.normpath(os.path.join(status_dir, '..', 'filters')),
            quick=False, create=True)

        self.fs = fs_worker
        self.app = app_worker
        self.search = search_worker
        self.metadata = metadata_worker
        self.imported = 0
        self.progress = self._no_progress(None)
        self.idle_running = False
        self.autotag_unloadable = set()

        self.lock = threading.Lock()
        self.keyword_batches = []
        self.keyword_batch_no = 0
        self.keyword_thread = None
        self.keywords = {}

        self.parser_settings = CommandParse.Settings(with_keywords=True)
        self.parser_settings.with_openpgp = False
        self.allow_network = True  # FIXME: Make configurable?

        assert(self.app and self.search)

    def run(self, *args, **kwargs):
        if hasattr(self.fs, 'forked'):
            self.fs.forked()
        return super().run(*args, **kwargs)

    def _get_email(self, metadata):
        try:
            if metadata.pointers[0].is_local_file:
                email = self.fs.email(metadata, full_raw=True)
            else:
                # FIXME: Passwords etc? Without them importing from IMAP fails.
                email = self.app.api_request(True,
                    RequestEmail(metadata=metadata, full_raw=True),
                    ).get('email', {})
            if email:
                return base64.b64decode(email.get('_RAW', b''))
        except Exception as e:
            logging.error('[import] Failed to load %s: %s' % (metadata.idx, e))
            return None

        logging.info('[import] Failed to load %s [%s]'
            % (metadata.idx, metadata,))
        return None

    def _mk_parser(self):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        def parse(email):
            return loop.run_until_complete(CommandParse.Parse(None, email,
                settings=self.parser_settings,
                allow_network=self.allow_network))
        return parse

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

        special = []  # FIXME, which tags are special? Any?
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
            logging.info('[import] Launching full import in background.')
            def _full_index():
                self._start_keyword_loop()
                if self.keyword_batches:
                    return
                try:
                    for tag_ns in [None]:  # FIXME
                        self._index_full_messages(None, tag_ns, self.progress)
                except Exception as e:
                    logging.exception('[import] Indexing failed: %s' % e)
                finally:
                    self.idle_running = False
            self.add_background_job(_full_index, which='full')

    def import_search(self,
            request_obj, initial_tags,
            tag_namespace=None, force=False, full=False, compact=False):
        return self.call('import_search',
            request_obj, initial_tags, tag_namespace,
            bool(force), full, compact)

    def api_autotag(self, tag_namespace, tags, search, **kwargs):
        if tags:
            tags = self._fix_tags_and_scope(tag_namespace, tags, _all=False)
        logging.debug('Requested autotag for %s on %s' % (tags, search))

        if not (search and 'results' in search):
            raise ValueError('Search terms are required')

        result = search['results']
        hits = dumb_decode(result['hits'])
        logging.debug(
            '[autotag] Requested autotagging for %s using "%s" (%d hits)'
             % (tags, result['terms'], sum(1 for hit in hits)))

        results = []
        autotaggers = []
        for tag in tags:
            autotagger = self.filters.get_autotagger(tag)
            if not autotagger:
                msg = 'Autotagging requsted, but not enabled for %s' % tag
                logging.error('[autotag] ' + msg)
                results.append(msg)
            elif not autotagger.is_trained():
                msg = ('Autotagging requested, but need more training for %s'
                    % tag)
                logging.error('[autotag] ' + msg)
                results.append(msg)
            else:
                autotaggers.append(autotagger)

        add_tags = {}
        rm_tags = {}
        if autotaggers:
            unloadable = checked = tagged = untagged = 0
            moggie_parse = self._mk_parser()
            res = self.metadata.metadata(hits, tags=None, threads=False)
            for md in res['metadata']:
                try:
                    eml = self._get_email(md)
                    if eml:
                        checked += 1
                        kws = moggie_parse(eml)['parsed']['_KEYWORDS']
                        for at in autotaggers:
                            if at.classify(kws) > at.threshold:
                                t = add_tags[at.tag] = add_tags.get(at.tag, [])
                                t.append(md.idx)
                                tagged += 1
                            else:
                                u = rm_tags[at.tag] = rm_tags.get(at.tag, [])
                                u.append(md.idx)
                                untagged += 1
                    else:
                        unloadable += 1
                except:
                    unloadable += 1
                    logging.exception('[autotag] Failed to parse %d' % md.idx)
            msg = ('Checked %d messages (%d failed), add/remove %d/%d tags.'
                % (checked, unloadable, tagged, untagged))
            logging.info('[autotag] ' + msg)
            results.append(msg)

        tagops = []
        tagops.extend(
            (['+%s' % tag], IntSet(idxs)) for tag, idxs in add_tags.items())
        tagops.extend(
            (['-%s' % tag], IntSet(idxs)) for tag, idxs in rm_tags.items())
        logging.debug('Tagops: %s' % tagops)
        results.append(self.search.tag(tagops,
            record_history=True,
            tag_namespace=tag_namespace))

        self.reply_json(results)

    def api_autotag_train(self, tag_ns, tags, search, compact, **kwargs):
        if tags:
            tags = self._fix_tags_and_scope(tag_ns, tags, _all=False)
        elif tag_ns:
            tags = [t for t in self.filters.autotaggers
                    if t.endswith('@' + tag_ns)]
        else:
            tags = [t for t in self.filters.autotaggers]

        if search and 'results' in search:
            result = search['results']
            all_hits = dumb_decode(result['hits'])
            auto = False
            logging.debug(
                '[autotag] Requested training for %s using "%s" (%d hits)'
                % (tags, result['terms'], sum(1 for hit in all_hits)))
        else:
            auto = True
            result = all_hits = None
            logging.debug('[autotag] Requested auto-training for %s' % (tags,))

        def _sample(seq, autotagger, is_spam):
            k = autotagger.min_trained * 5
            seq = set(seq)
            seq -= set(autotagger.spam_ids if is_spam else autotagger.ham_ids)
            seq = list(seq)
            if len(seq) < k:
                return sorted(seq)
            else:
                return sorted(random.sample(seq, k))

        self.filters.load()
        plan = {}
        versions = {}
        for tag in tags:
            res = result
            autotagger = self.filters.get_autotagger(tag, create=(not auto))
            if auto:
                if autotagger and autotagger.training_auto:
                    req = autotagger.auto_train_search_obj(None)
                    res = self.search.search(req['terms'],
                        tag_namespace=tag_ns,
                        mask_deleted=req.get('mask_deleted', False),
                        mask_tags=req.get('mask_tags', []),
                        with_tags=req.get('with_tags', False))
                    versions[tag] = res['version']
                else:
                    logging.debug('[autotag] Not auto-training %s' % tag)
                    continue
                ham_ids = dumb_decode(res['hits'])
            else:
                ham_ids = IntSet(copy=all_hits)

            tag_info = res['tags'].get(tag)
            if tag_info:
                spam_ids = dumb_decode(tag_info[1])
                ham_ids -= spam_ids
            else:
                spam_ids = []

            plan[tag] = (
                _sample(spam_ids, autotagger, True),
                _sample(ham_ids, autotagger, False))

        wanted = IntSet()
        for spam_ids, ham_ids in plan.values():
            wanted |= spam_ids
            wanted |= ham_ids

        moggie_parse = self._mk_parser()
        unloadable = self.autotag_unloadable
        keywords = {}
        res = self.metadata.metadata(wanted, tags=None, threads=False)
        for md in res['metadata']:
            if md.idx in unloadable:
                continue
            try:
                eml = self._get_email(md)
                if eml:
                    keywords[md.idx] = moggie_parse(eml)['parsed']['_KEYWORDS']
                else:
                    unloadable.add(md.idx)
            except:
                logging.exception('[autotag] Failed to parse %d' % md.idx)

        results = []
        for tag, (spam_ids, ham_ids) in plan.items():
            autotagger = self.filters.get_autotagger(tag, create=False)
            justdoit = (not auto) or (not autotagger.is_trained())
            spam_ids = set(spam_ids) - unloadable
            ham_ids = set(ham_ids) - unloadable
            ignored = 0
            # When auto-training, we only train on messages which we have
            # seen before (user corrections) or messages which are already
            # likely to be recognized (to gradually pick up new keywords).
            # If this is a user request (not auto), we "just do it".
            for _id in (i for i in spam_ids if i in keywords):
                if (justdoit
                        or autotagger.is_known(_id)
                        or (autotagger.classify(keywords[_id]) > 0.8)):
                    autotagger.learn(_id, keywords[_id], is_spam=True)
                else:
                    ignored += 1
            for _id in (i for i in ham_ids if i in keywords):
                if (justdoit
                        or autotagger.is_known(_id)
                        or (autotagger.classify(keywords[_id]) < 0.2)):
                    autotagger.learn(_id, keywords[_id], is_spam=False)
                else:
                    ignored += 1

            msg = ('Trained %s with %d pos, %d neg, %d ignored, %d failed.'
                % (tag, len(spam_ids), len(ham_ids), ignored, len(unloadable)))
            logging.info('[autotag] ' + msg)
            results.append(msg)

            if tag in versions:
                autotagger.trained_version = versions[tag]
            # FIXME: autotagger.prune()
            if compact:
                autotagger.compact()
            self.filters.save_autotagger(tag)

        self.reply_json(results)

    def api_autotag_classify(self, tag_ns, tags, keywords, **kwargs):
        if tags:
            tags = self._fix_tags_and_scope(tag_ns, tags, _all=False)
        elif tag_ns:
            tags = [t for t in self.filters.autotaggers
                    if t.endswith('@' + tag_ns)]
        else:
            tags = [t for t in self.filters.autotaggers]

        results = {}
        for tag in tags:
            autotagger = self.filters.get_autotagger(tag)
            if autotagger:
                rank, clues = autotagger.classify(keywords, evidence=True)
                results[tag] = (rank, dict(clues))

        self.reply_json(results)

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
                logging.exception('[import] Failed to import search')
        self.add_background_job(background_import_search)
        self.reply_json({'running': True})

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
                logging.debug('[import] keyword loop: exiting')
                return

            logging.debug(
                '[import] keyword loop: Entered loop (keywords=%d)'
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
                            logging.debug('[import] keyword loop: exiting early')
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
                logging.debug(
                    '[import] Marking batch %d complete (%s): %s messages'
                    % (bno, tag, len(idxs)))
                self.search.del_results([[idxs, tag]], wait=True)

            # 6. Report progress
            self._notify_progress(self.progress)

    def _start_keyword_loop(self, after=None):
        with self.lock:
            if self.keyword_thread and self.keyword_thread.is_alive():
                pass
            else:
                logging.debug('[import] keyword loop: Launching thread')
                def _loop_runner():
                    try:
                        self._keyword_loop(delay=after)
                    except:
                        logging.exception('[import] Keyword loop crashed')
                    self.keyword_thread = None
                thr = threading.Thread(target=_loop_runner)
                self.keywords = self.keywords or {}
                self.keyword_thread = thr
                self.keyword_thread.daemon = True
                self.keyword_thread.start()
        return self.keywords

    def _index_full_messages2(self, email_idxs, tag_namespace, progress, old):
        in_queue = 'in:incoming-old' if old else 'in:incoming'
        incoming = self._fix_tags_and_scope(tag_namespace, [in_queue],
            _all=False)[0]
        if email_idxs:
            email_idxs = list(self.search.intersect(incoming, email_idxs))
        else:
            all_incoming = self.search.search(incoming)['hits']
            email_idxs = list(dumb_decode(all_incoming))
        if not email_idxs:
            logging.debug('[import] No messages to process (%s).' % incoming)
            return

        progress['emails_new'] = len(email_idxs)
        email_idxs = email_idxs[:self.BATCH_SIZE_FULL]
        progress['pending'] += 1

        ntime, bc, ec = int(time.time()), 0, 0

        moggie_parse = self._mk_parser()
        self.filters.load()
        message_batches = []
        for i in range(0, len(email_idxs), self.BATCH_SIZE):
            last_loop = len(email_idxs) <= i+self.BATCH_SIZE

            idx_batch = email_idxs[i:i+self.BATCH_SIZE]
            logging.info(
                '[import] Processing [%d..%d]/%d (last_loop=%s, keywords=%d)'
                % (
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
                    logging.exception('[import] Failed to load %s' % (md,))
                    continue

                # Parse the e-mail and extract keywords.
                # This uses the same logic as `moggie parse`.
                email = moggie_parse(email)['parsed']
                kws = set(email['_KEYWORDS'])

                # 3. Run the filtering logic to mutate keywords/tags
                if not old:
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
                    logging.info('[import] Compacting metadata')
                    self.metadata.compact(full=True)
                    logging.info('[import] Compacting search index')
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

