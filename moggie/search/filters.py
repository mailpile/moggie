# This is the logic which governs how Moggie filters e-mails
#
# FIXME/TODO:
#
#   - Support encrypted filter rules!
#   - Make it possible for filters to key off the tag_namespace or other
#     initial tags set at import time; currently this won't work?
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
import logging
import os
import re
import time
import traceback


DEFAULT_NEW_FILTER_SCRIPT = """\
# These are the default moggie filter rules. Edit to taste!
FILTER_RULE('DEFAULT')


# Add to inbox by default; later steps may undo.
add_tags('inbox')


# Treat new messages as unread, unless headers tell us otherwise.
if 'status:o' in keywords:
    add_tags('read')


# Check if we think message is spam, unless already classified
if ('in:spam' not in keywords) and ('in:notspam' not in keywords):
    run_autotagger('spam')

if 'in:spam' in keywords:
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

    def run_autotagger(self, which=None, skip=None):
        pass  # FIXME

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


class FilterRule:
    def __init__(self, script=None, filename=None, mock_debug=None):
        self.name = ''
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
    def Load(cls, rulestr, **kwargs):
        return cls(rulestr.strip() or 'pass', **kwargs).validate()

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
    def __init__(self, mock_os=None, mock_open=None, mock_exc=None):
        self.os = mock_os or os
        self.open = mock_open or open
        self.on_exc = mock_exc or logging.debug
        self.loaded = {}
        self.filter_dirs = []
        self.pys = {
            'DEFAULT': FilterRule.Load(DEFAULT_NEW_FILTER_SCRIPT)}

    def load(self, filter_dir=None, quick=True, create=False):
        if filter_dir and (filter_dir not in self.filter_dirs):
            self.filter_dirs.append(filter_dir)

        for fdir in ([filter_dir] if filter_dir else self.filter_dirs):

            if create and not self.os.path.exists(fdir):
                os.mkdir(fdir, 0o0700)
                with open(os.path.join(fdir, 'default.py'), 'w') as fd:
                    fd.write(DEFAULT_NEW_FILTER_SCRIPT)
                logging.info('Created default filter rule in %s' % fdir)

            for fn in self.os.listdir(fdir):
                if fn.endswith('.py'):
                    fpath = self.os.path.join(fdir, fn)
                    fd = None
                    try:
                        mtime = self.os.path.getmtime(fpath)
                        if quick and mtime == self.loaded.get(fpath, 0):
                            continue

                        fd = self.open(fpath)
                        fr = FilterRule.Load(fd.read(), filename=fn)
                        self.pys[fr.name] = fr
                        self.loaded[fpath] = mtime
                    except FilterError as e:
                        self.on_exc(e)  # FIXME: Failed to compile rule
                    except OSError as e:
                        self.on_exc(e)  # FIXME: Access denied
                    finally:
                        if fd:
                            fd.close()
        return self

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
                logging.debug('Applying filter rule: %s' % fn)
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
