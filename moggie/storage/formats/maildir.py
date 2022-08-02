import os
import time

from ...email.metadata import Metadata
from ...email.headers import parse_header
from ...email.util import quick_msgparse, make_ts_and_Metadata
from . import tag_path


COUNTER = 0


class FormatMaildir:
    NAME = 'maildir'
    TAG = b'md'

    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
        if not is_dir:
            return False
        for sub in (b'cur', b'new', b'tmp'):
            if not os.path.join(key, sub) in parent:
                return False
        return True

    def __init__(self, parent, path, container):
        self.parent = parent
        self.path = path
        self.basedir = tag_path(*self.path)
        self.sep = bytes(os.path.sep, 'us-ascii')

    def _key_to_paths(self, key):
        for sub in (b'cur', b'new'):
            yield os.path.join(self.basedir, sub + key)

    def __contains__(self, key):
        try:
            for p in self._key_to_paths(key):
                if p in self.parent:
                    return True
        except (OSError, ValueError):
            pass
        return False

    def __getitem__(self, key):
        for p in self._key_to_paths(key):
            if p in self.parent:
                return self.parent[p][:]
        raise KeyError('Not found: %s' % key)

    def __delitem__(self, key):
        for p in self._key_to_paths(key):
            if p in self.parent:
                del self.parent[p]
                return
        raise KeyError('Not found: %s' % key)

    def __iadd__(self, data):
        self.append(data)
        return self

    def append(self, data, force_key=None):
        if isinstance(data, str):
            data = bytes(data, 'utf-8')

        while True:
            if force_key is None:
                global COUNTER
                key = b'%s%x.%x' % (self.sep, int(time.time() * 1000), COUNTER)
                COUNTER += 1
            else:
                key = force_key
            tmpfile = os.path.join(self.basedir, b'tmp' + key)
            curfile = os.path.join(self.basedir, b'cur' + key)
            if tmpfile not in self.parent and curfile not in self.parent:
                break
            if force_key:
                raise ValueError('Key already in use')

        self.parent[tmpfile] = data
        self.parent.rename(tmpfile, curfile)
        return self.get_tagged_path(key)

    def get(self, key, default=None, **kwargs):
        try:
            return self[key]
        except (IndexError, ValueError):
            return default

    def get_tagged_path(self, key):
        path = self.path + [(self.TAG, key)]
        return tag_path(*path)

    def __setitem__(self, key, value):
        if isinstance(value, str):
            value = bytes(value, 'utf-8')

        for p in self._key_to_paths(key):
            if p in self.parent:
                self.parent[p] = value
                return

        self.append(value, force_key=key)    

    def full_keys(self, skip=0):
        for sub in (b'new', b'cur'):
            files = self.parent.listdir(os.path.join(self.basedir, sub))
            for fn in sorted(list(files)):
                if skip > 0:
                    skip -= 1
                else:
                    yield (sub, fn)

    def keys(self, skip=0):
        for sub, fn in self.full_keys(skip=skip):
            yield (self.sep + fn)

    def __iter__(self):
        return self.keys()

    def __len__(self):
        return sum(1 for sub, fn in self.full_keys())

    def iter_email_metadata(self, skip=0, iterator=None):
        lts = 0
        now = int(time.time())
        for sub, fn in self.full_keys(skip=skip):
            try:
                obj = self.parent[os.path.join(self.basedir, sub, fn)]
                hend, hdrs = quick_msgparse(obj, 0)
                path = self.get_tagged_path(self.sep + fn)
                lts, md = make_ts_and_Metadata(
                    now, lts, obj[:hend],
                    [Metadata.PTR(Metadata.PTR.IS_FS, path, len(obj))],
                    hdrs)
                yield md
            except (KeyError, ValueError, TypeError):
                pass


if __name__ == "__main__":
    import os, sys
    from ..files import FileStorage

    fs = FileStorage()
    fs.RegisterFormat(FormatMaildir)

    os.system('rm -rf /tmp/maildir-test')
    for p in ('cur', 'new', 'tmp'):
        os.system('mkdir -p /tmp/maildir-test/'+p)
    assert(FormatMaildir.Magic(fs, b'/tmp/maildir-test', None, is_dir=True))

    bc = FormatMaildir(fs, [b'/tmp/maildir-test'], None)
    fn = bc.append(b'Hello world')
    assert(b'[md:' in fn)
    assert(fn in fs)
    assert(fs[fn][:] == b'Hello world')
    assert(len(list(bc.keys())) == 1)
    del fs[fn]
    assert(fn not in fs)
    assert(len(list(bc.keys())) == 0)

    print('Tests passed OK')

    for path in sys.argv[1:]:
        path = bytes(path, 'utf-8')
        md = FormatMaildir(fs, [path], None)
        print('=== %s (%d) ===' % (path, len(md)))
        print('%s' % '\n'.join('%s' % m for m in md.iter_email_metadata()))
        print('=== %s (%d) ===' % (path, len(md)))

