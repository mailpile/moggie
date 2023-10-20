# This is the logic which governs how Moggie filters e-mails
#
# FIXME/TODO:
#
#   - Move autotag configuration out of the JSON, into the main config!
#   - Support encrypted filter rules!
#   - Make it possible for filters to key off the tag_namespace or other
#     initial tags set at import time; currently this won't work?
#
#   - We want to make it super easy to create dedicated processing
#     for 1) different attachments, 2) different senders. Examples:
#     - Create calendar entries based on .ics files
#     - Create calendar entries based on flight itinerary emails
#     - Auto-save invoices to a folder
#     - Mailing list automation?
#     - Handle incoming patches for the git nerds
#     - Many of the above rules should be easy for 3rd parties to
#       contribute. So one file per rule?
#   - Need a helper for processing attachments
#   - Need a helper for checking DKIM, unless we always do that?
#   - Probably need a richer set of FILTER_RULE(...) arguments, so we
#     can autogenerate/edit filter rules with a high-level user interface.
#
import copy
import logging
import os
import random
import re
import time
import traceback
import hashlib

from ..util.dumbcode import from_json, to_json
from ..util.spambayes import Classifier


DEFAULT_NEW_FILTER_SCRIPT = """\
# These are the default moggie filter rules. Edit to taste!
FILTER_RULE('DEFAULT')


# Add to inbox by default; later steps may undo.
add_tags('inbox')


# Treat new messages as unread, unless headers tell us otherwise.
if 'status:o' in keywords:
    add_tags('read')


# Check if we think message is spam, unless already classified
if ('in:junk' not in keywords) and ('in:notjunk' not in keywords):
    run_autotagger('in:junk')
if 'in:junk' in keywords:
    remove_tag('inbox')
"""


class FilterError(Exception):
    pass


class FilterEnv(dict):
    def __init__(self, rule, **kwargs):
        dict.__init__(self, kwargs)
        self.py = rule
        self.keywords = self.get('keywords')
        self.tag_namespace = self.get('tag_namespace')
        self.autotag_done = set()
        self.called_filter_rule = False
        self.update({
            'FILTER_RULE': self.set_filter_info,
            'logging': logging,
            'now': time.time(),
            'add_tag': self.add_tags,
            'add_tags': self.add_tags,
            'remove_tag': self.remove_tags,
            'remove_tags': self.remove_tags,
            'add_keyword': self.add,
            'add_keywords': self.add,
            'remove_keyword': self.remove,
            'remove_keywords': self.remove,
            'run_autotagger': self.run_autotagger})

    def reset(self, keywords, tag_namespace):
        self.keywords = keywords
        self.tag_namespace = tag_namespace
        self.autotag_done = set()
        self.called_filter_rule = False
        self.update({
            'now': time.time(),
            'keywords': keywords})
        if 'run' in self:
            del self['run']
        return self

    def set_filter_info(self, name,
            require_all=None, require_any=None, exclude=None,
            tag_namespace=None,
            settings=None):
        if self.called_filter_rule:
            raise FilterError('Called FILTER_RULE twice!')
        self.py.name = name
        self.py.tag_namespace = tag_namespace
        self.py.req_all = set((require_all or '').lower().strip().split())
        self.py.req_any = set((require_any or '').lower().strip().split())
        self.py.exclude = set((exclude or '').lower().strip().split())
        self.py.settings = settings or {}
        self.called_filter_rule = True

    def run_autotagger(self, tag):
        tag = 'in:%s' % (tag.split(':')[-1])
        if self.tag_namespace and ('@' not in tag):
            tag += '@%s' % self.tag_namespace
        at = self.py.engine.get_autotagger(tag, create=False)
        if at is None:
            self.py.engine.log_once(
                logging.error, '[import] Failed to load autotagger: %s' % tag)
        else:
            rank = at.classify(self.keywords)
            if rank > at.threshold:
                self.keywords.add(tag)
                logging.debug(
                    '[import] Auto-tagged (rank=%.3f) with: %s' % (rank, tag))

    def remove(self, *kwsets):
        for kws in kwsets:
            self.keywords -= set(kws if isinstance(kws, (list, set)) else [kws])

    def add(self, *kwsets):
        for kws in kwsets:
            for kw in (kws if isinstance(kws, list) else [kws]):
                self.keywords.add(kw.lower())

    def add_tags(self, *tagsets):
        for ts in tagsets:
            for tag in (ts if isinstance(ts, list) else [ts]):
                if self.tag_namespace:
                    self.keywords.add('in:%s@%s' % (tag, self.tag_namespace))
                else:
                    self.keywords.add('in:%s' % (tag,))

    def remove_tags(self, *tagsets):
        for ts in tagsets:
            for tag in (ts if isinstance(ts, list) else [ts]):
                if self.tag_namespace:
                    rm = 'in:%s@%s' % (tag, self.tag_namespace)
                else:
                    rm = 'in:%s' % (tag,)
                try:
                    self.keywords.remove(rm)
                except KeyError:
                    pass


class AutoTagger:
    """
    This class implements the auto-tagging backend.

    FIXME:
        - Use a secure salt for the hash
        - Allow the user to specify custom rules to ignore certain keywords
          or force negative or positive results.
    """
    DEF_MIN_TRAINED = 250
    DEF_THRESHOLD = 0.6
    DEF_CLASSIFIER = 'spambayes'
    DEF_TRAINING_AUTO = True

    SKIP_RE = re.compile(
        '(^\d+$'
        '|^.{0,3}$'
        '|^[^:]{30,}$'
        '|^(email|to|from|cc|date|day|month|year|msgid):'
        '|.*@'
        ')')

    def __init__(self, tag=None, salt='FIXME: Better than nothing'):
        self.tag = tag
        self.salt = bytes(salt, 'utf-8') if isinstance(salt, str) else salt
        self.classifier_type = self.DEF_CLASSIFIER  # Others? Maybe someday!
        self.classifier = Classifier()
        self.min_trained = self.DEF_MIN_TRAINED
        self.threshold = self.DEF_THRESHOLD
        self.training_auto = self.DEF_TRAINING_AUTO
        self.trained_version = 0
        self.spam_ids = []
        self.ham_ids = []
        self.info = {}

    def from_json(self, raw_json):
        self.info = info = from_json(raw_json)
        self.tag = info.pop('tag')
        self.spam_ids = info.pop('spam_ids', [])
        self.ham_ids = info.pop('ham_ids', [])
        self.min_trained = int(info.pop('min_trained', self.DEF_MIN_TRAINED))
        self.threshold = float(info.pop('threshold', self.DEF_THRESHOLD))
        self.training_auto = bool(info.pop('training_auto', self.DEF_TRAINING_AUTO))
        self.trained_version = info.pop('trained_version', 0)
        self.classifier_type = info.pop('classifier', self.DEF_CLASSIFIER)
        self.classifier.load(info.pop('data', []))
        return self

    def to_json(self):
        info = copy.copy(self.info)
        info.update({
            'tag': self.tag,
            'spam_ids': self.spam_ids,
            'ham_ids': self.ham_ids,
            'min_trained': self.min_trained,
            'threshold': self.threshold,
            'training_auto': self.training_auto,
            'trained_version': self.trained_version,
            'classifier': self.classifier_type,
            'data': list(self.classifier)})
        return to_json(info)

    @classmethod
    def MakeSearchObject(self, context=None, terms=None):
        from ..api.requests import RequestSearch
        req = RequestSearch(context=context, terms=terms)
        req['mask_deleted'] = False
        req['with_tags'] = True
        req['mask_tags'] = []
        req['uncooked'] = True
        return req

    def auto_train_search_obj(self, ctx):
        if not self.training_auto:
            raise ValueError('Auto-training is disabled for %s' % self.tag)
        return self.MakeSearchObject(
            context=ctx,
            terms='version:%d..' % (self.trained_version + 1,))

    def is_trained(self):
        return (len(self.ham_ids) >= self.min_trained
            and len(self.spam_ids) >= self.min_trained)

    def obfuscate(self, keywords):
        if not self.salt:
            return keywords
        def _obfu(kw):
            if (':' not in kw) or (kw[:8] == 'subject:'):
                kw = hashlib.sha1(bytes(kw, 'utf-8') + self.salt)
                kw = kw.hexdigest()[:12]
            return kw
        return [_obfu(kw) for kw in keywords]

    def is_known(self, _id):
        return (_id in self.spam_ids) or (_id in self.ham_ids)

    def classify(self, keywords, evidence=False):
        if not self.is_trained():
            dbg = 'untrained/%d/%d' % (len(self.spam_ids), len(self.ham_ids))
            return (
                (0.5, [('%s:%s' % (self.classifier_type, dbg), 0.5)])
                if evidence else 0.5)

        special_keywords = [k for k in keywords if ':' in k]
        for kws, minkw, confidence in (
                (special_keywords, 5, 0.95),
                (keywords, 0, 0)):
            if len(kws) < minkw:
                continue
            obfuscated = self.obfuscate(kws)
            delta = confidence / 2.0
            if evidence:
                kw_map = dict(zip(obfuscated, kws))
                p, clues = self.classifier.classify(obfuscated, evidence=True)
                if p <= (0.5 - delta) or p >= (0.5 + delta):
                    return p, ((kw_map[k], v) for k, v in clues if k in kw_map)
            else:
                p = self.classifier.classify(obfuscated)
                if p <= (0.5 - delta) or p >= (0.5 + delta):
                    return p

    def learn(self, _id, keywords, is_spam=True):
        set_yes = self.spam_ids if is_spam else self.ham_ids
        set_no = self.ham_ids if is_spam else self.spam_ids
        keywords = self.obfuscate(
            [k.lower() for k in keywords if not self.SKIP_RE.match(k)])
        if _id in set_no:
            self.classifier.unlearn(keywords, not is_spam)
            set_no.remove(_id)
        self.classifier.learn(keywords, is_spam)
        set_yes.append(_id)

    def compact(self):
        target_size = 100 * self.min_trained
        current_size = len(self.spam_ids) + len(self.ham_ids)
        ratio = 1 - (target_size / current_size)

        prune_spam = round(len(self.spam_ids) * ratio)
        prune_ham = round(len(self.ham_ids) * ratio)
        if ((ratio < 0.05)
                or (current_size < target_size)
                or not (prune_spam and prune_ham)):
            logging.info(
                '[autotag] Trained set is too small, compacting aborted.')
            return False

        dropped = self.classifier.decay(ratio)
        self.spam_ids = self.spam_ids[prune_spam:]
        self.ham_ids = self.ham_ids[prune_ham:]
        logging.info(
            '[autotag] Decayed weights by %.2f%% (~%d emails), dropped %d terms'
            % (100 * ratio, prune_spam + prune_ham, dropped))

        return True


class FilterRule:
    def __init__(self,
            script=None, engine=None, filename=None, mock_debug=None):
        self.name = ''
        self.engine = engine
        self.req_any = set()
        self.req_all = set()
        self.exclude = set()
        self.settings = {}
        self.debug = mock_debug or logging.debug
        self.error = mock_debug or logging.error
        self.tag_namespace = None

        self.raw_script = script
        self.env = FilterEnv(self)
        self.script = self._compile(filename)

    @classmethod
    def Load(cls, rulestr, *args, **kwargs):
        return cls(rulestr.strip() or 'pass', *args, **kwargs).validate()

    def _compile(self, filename):
        raw = self.raw_script or DEFAULT_NEW_FILTER_SCRIPT
        script = (
            'def run(keywords, metadata, email):\n  ' +
            '\n  '.join(raw.splitlines())
            ).strip().replace('  \n', '\n')
        try:
            exec(script, self.env)
            return self.env['run']
        except:
            raise FilterError('Compile(%s) failed' % (filename or '-'))

    def validate(self):
        # FIXME: Run the filter script against mock metadata and parse tree.
        self.filter(None, set(), None, {}, _tag_ns_check=False)
        if not self.name:
            self.error('Failed to validate filter rule: %s' % self.name)
            raise FilterError('FILTER_RULE was never called!')
        else:
            self.debug('Validated new filter rule: %s' % self.name)
        return self

    def filter(self, tag_namespace, keywords, metadata, parsed_email,
            _tag_ns_check=True):
        if (_tag_ns_check
                and self.tag_namespace
                and self.tag_namespace != tag_namespace):
            raise FilterError('Tag namespace mismatch: %s != %s' % (
                tag_namespace, self.py.tag_namespace))
        try:
            self.env.reset(keywords, tag_namespace)
            self.script(keywords, metadata, parsed_email)
        except Exception as e:
            raise FilterError(str(e))
        return keywords


class FilterEngine:
    def __init__(self,
            moggie=None, encryption_keys=None,
            mock_os=None, mock_open=None, mock_exc=None):
        self.moggie = moggie
        self.aes_keys = encryption_keys
        self.os = mock_os or os
        self.open = mock_open or open
        self.on_exc = mock_exc or logging.debug
        self.loaded = {}
        self.filter_dirs = []
        self.writable_dirs = []
        self.autotaggers = {}
        self.logged = set()
        self.pys = {
            'DEFAULT': FilterRule.Load(DEFAULT_NEW_FILTER_SCRIPT, self)}

    def log_once(self, log_method, message):
        if message in self.logged:
            return
        self.logged.add(message)
        log_method(message)

    def load(self, filter_dir=None, quick=True, create=False):
        if filter_dir and (filter_dir not in self.filter_dirs):
            self.filter_dirs.append(filter_dir)
            if create:
                self.writable_dirs.append(filter_dir)

        # FIXME: Support loading from encrypted zips - especially the
        #        autotagger databases leak private message content.

        for fdir in ([filter_dir] if filter_dir else self.filter_dirs):

            if create and not self.os.path.exists(fdir):
                os.mkdir(fdir, 0o0700)
                with open(self.os.path.join(fdir, 'default.py'), 'w') as fd:
                    fd.write(DEFAULT_NEW_FILTER_SCRIPT)
                logging.info('Created default filter rule in %s' % fdir)

            for fn in self.os.listdir(fdir):
                try:
                    fpath = self.os.path.join(fdir, fn)
                    mtime = self.os.path.getmtime(fpath)
                    if quick and mtime == self.loaded.get(fpath, 0):
                        continue
                except OSError as e:
                    self.on_exc(e)
                    continue

                if fn.endswith('.py'):
                    fd = None
                    try:
                        with self.open(fpath) as fd:
                            fr = FilterRule.Load(fd.read(), self, filename=fn)
                        self.pys[fr.name] = fr
                        self.loaded[fpath] = mtime
                    except FilterError as e:
                        self.on_exc(e)  # FIXME: Failed to compile rule
                    except OSError as e:
                        self.on_exc(e)  # FIXME: Access denied
                    finally:
                        if fd:
                            fd.close()

                elif fn.endswith('.atag'):
                    try:
                        self.load_autotagger(fpath)
                        self.loaded[fpath] = mtime
                    except (OSError, ValueError, KeyError) as e:
                        self.on_exc(e)

        return self

    def load_autotagger(self, fpath):
        with self.open(fpath, 'rb') as fd:
            json_data = fd.read()
        at = AutoTagger().from_json(json_data)
        self.autotaggers[at.tag] = (fpath, at)
        logging.info('[import] Loaded autotagging rules for %s: %s'
            % (at.tag, fpath))

    def save_autotagger(self, tag):
        # FIXME: This should encrypt the contents!
        try:
            fpath, at = self.autotaggers[tag]
            dump = at.to_json()
            with self.open(fpath, 'w') as fd:
                fd.write(dump)
            logging.info('[import] Updated autotagging rules for %s: %s'
                % (at.tag, fpath))
            return True
        except:
            logging.exception('[import] Failed to save: %s' % fpath)
            return False

    def get_autotagger(self, tag, create=False):
        if tag not in self.autotaggers:
            if not create:
                return None
            at = AutoTagger(tag)
            fpath = None
            while self.writable_dirs:
                fname = '%x.atag' % random.randint(0x10000, 0xffffffff)
                fpath = self.os.path.join(self.writable_dirs[0], fname)
                if not self.os.path.exists(fpath):
                    break
            if fpath:
                self.autotaggers[tag] = (fpath, at)
                if not self.save_autotagger(tag):
                    del self.autotaggers[tag]
                    return None
            else:
                return None
        return self.autotaggers[tag][1]

    def filter(self, tag_namespace, keywords, metadata, parsed_email,
            which=None):
        if which is not None:
            return self.pys[which].filter(
                tag_namespace, keywords, metadata, parsed_email)

        for fn, fr in self.pys.items():
            if fr.req_all and (keywords & fr.req_all) != fr.req_all:
                continue
            if fr.req_any and (keywords & fr.req_any):
                continue
            if (not fr.exclude) or not (keywords & fr.exclude):
                self.log_once(logging.debug, 'Applying filter rule: %s' % fn)
                keywords = fr.filter(
                    tag_namespace, keywords, metadata, parsed_email)

        return keywords


if __name__ == '__main__':
    # Configure a deliberately broken filter rule.
    fr = FilterRule(DEFAULT_NEW_FILTER_SCRIPT + """

remove_keyword('bogon')
add_keywords('fungible', 'bogon', ['flip', 'flop'])
remove_keyword('bogon')

# This evaluates to None, so we end up with 'none' as a keyword
add_keyword(str(metadata))

# Crash! Should raise the expected exception
crash_me()
add_keywords('notreached')

""")

    # Configure a filter engine using the broken rule.
    fe = FilterEngine(mock_exc=lambda e: None)
    fe.pys['DEFAULT'] = fr

    kw = set(['status:o'])
    for tns in (None, 'testspace'):
        try:
            fe.filter(tns, kw, None, None)
            assert(not 'reached')
        except FilterError:
            pass

    assert('in:inbox' in kw)
    assert('in:inbox@testspace' in kw)
    assert('in:read' in kw)
    assert('in:read@testspace' in kw)
    assert('none' in kw)
    assert('flip' in kw)
    assert('fungible' in kw)
    assert('notreached' not in kw)
    assert('bogon' not in kw)

    # The FILTER_RULE directive is required
    try:
        FilterRule.Load('', mock_debug=lambda e: None)
        assert(not 'reached')
    except FilterError:
        pass

    # But we only want one...
    try:
        FilterRule.Load("""
FILTER_RULE("test")
FILTER_RULE("test")
           """, mock_debug=lambda e: None)
        assert(not 'reached')
    except FilterError:
        pass

    # No-op rules are allowed!
    try:
        FilterRule.Load('FILTER_RULE("test")')
    except FilterError:
        pass


    import io
    excs = []
    opened = {}
    class MockOS:
        class path:
            @classmethod
            def join(cls, *args):
                return os.path.join(*args)
            @classmethod
            def getmtime(cls, fn):
                return 1999
            @classmethod
            def exists(cls, fn):
                return (fn in ['/tmp', '/tmp/default.py', '/tmp/extra.py'])
        @classmethod
        def open(cls, fn):
            opened[fn] = opened.get(fn, 0) + 1
            if 'default' in fn:
                return io.StringIO(DEFAULT_NEW_FILTER_SCRIPT)
            else:
                return io.StringIO('bogus script fail')
        @classmethod
        def listdir(cls, fn):
            return ['default.py', 'extra.py']

    fe = FilterEngine(
        mock_os=MockOS,
        mock_open=MockOS.open,
        mock_exc=lambda e: excs.append(e))
    fe.load('/tmp', create=True)
    fe.load('/tmp')
    assert(opened['/tmp/default.py'] == 1)  # Not two: mtimes are checked.
    assert(opened['/tmp/extra.py'] == 2)    # Always tried, always fails
    assert(len(excs) == 2)
    assert('extra.py' in str(excs[0]))

    print('Tests passed OK')
