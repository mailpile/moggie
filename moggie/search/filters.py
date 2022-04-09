# This is the logic which governs how Moggie filters e-mails
#
import re
import time


DEFAULT_NEW_FILTER_SCRIPT = """\
# By default, treat new messages as unread, add to Inbox
add_tags('unread', 'inbox')

if 'status:o' in keywords:
    remove_tag('unread')

# Check if we think message is spam
run_autotagger('spam')
if 'in:spam' in keywords:
    remove_tag('inbox')

# Run the rest of the autotaggers
run_autotagger()

"""


class FilterError(Exception):
    pass


class FilterEnv(dict):
    def __init__(self, **kwargs):
        dict.__init__(self, kwargs)
        self.keywords = self.get('keywords')
        self.autotag_done = set()
        self.update({
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

    def reset(self, keywords):
        self.keywords = keywords
        self.autotag_done = set()
        self.update({
            'now': time.time(),
            'keywords': keywords})
        if 'run' in self:
            del self['run']
        return self

    def run_autotagger(self, which=None):
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
                self.keywords.add('in:%s' % (tag,))

    def remove_tags(self, *tagsets):
        for ts in tagsets:
            for tag in (ts if isinstance(ts, list) else [ts]):
                self.keywords.remove('in:%s' % (tag,))


class FilterEngine:
    def __init__(self, script=None):
        self.raw_script = script
        self.env = FilterEnv()
        self.script = self._compile()

    def _compile(self):
        raw = self.raw_script or DEFAULT_NEW_FILTER_SCRIPT
        script = (
            'def run(keywords, metadata, email):\n  ' +
            '\n  '.join(raw.splitlines())
            ).strip().replace('  \n', '\n')
        try:
            exec(script, self.env)
            return self.env['run']
        except:
            raise FilterError('Compile failed')

    def validate(self):
        # FIXME: Run the filter script against mock metadata and parse tree.
        return self

    def filter(self, keywords, metadata, parsed_email):
        try:
            self.env.reset(keywords)
            self.script(keywords, metadata, parsed_email)
        except Exception as e:
            raise FilterError(str(e))
        return keywords


if __name__ == '__main__':
    fe = FilterEngine(DEFAULT_NEW_FILTER_SCRIPT + """

remove_keyword('bogon')
add_keywords('fungible', 'bogon', ['flip', 'flop'])
remove_keyword('bogon')

# This evaluates to None, so we end up with 'none' as a keyword
add_keyword(str(metadata))

# Crash! Should raise the expected exception
crash_me()
add_keywords('notreached')

""")
    kw = set(['status:o'])
    try:
        fe.validate()
        fe.filter(kw, None, None)
        assert(not 'reached')
    except FilterError:
        pass

    assert('in:inbox' in kw)
    assert('in:unread' not in kw)
    assert('none' in kw)
    assert('flip' in kw)
    assert('fungible' in kw)
    assert('notreached' not in kw)
    assert('bogon' not in kw)

    print('Tests passed OK')
