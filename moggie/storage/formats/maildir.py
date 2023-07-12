import os
import time

from ...email.metadata import Metadata
from ...email.headers import parse_header
from ...email.util import quick_msgparse, make_ts_and_Metadata
from ...email.util import split_maildir_meta
from ...email.util import mk_maildir_idx, unpack_maildir_idx
from . import tag_path


COUNTER = 0


class FormatMaildir:
    NAME = 'maildir'
    TAG = b'md'

    MAGIC_CHECKS = (b'cur', b'new', b'tmp')

    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
        if not is_dir:
            return False
        for sub in cls.MAGIC_CHECKS:
            if not os.path.join(key, sub) in parent:
                return False
        return True

    def __init__(self, parent, path, container, needs_reindexing_cb=None):
        self.parent = parent
        self.path = path
        self.basedir = tag_path(*self.path)
        self.sep = bytes(os.path.sep, 'us-ascii')
        self.needs_reindexing_cb = needs_reindexing_cb or (lambda *s: True)

    def _find_by_idx(self, full_idx):
        full_idx_pos, full_idx_hash = unpack_maildir_idx(full_idx)
        partial_match = None
        for i, (sub, fn) in enumerate(self.full_keys()):
            idx = mk_maildir_idx(fn, i)
            if idx == full_idx:
                return sub, fn
            idx_pos, idx_hash = unpack_maildir_idx(idx)
            if idx_hash == full_idx_hash:
                partial_match = sub, fn
        if partial_match is not None:
            self.needs_reindexing_cb(self)
            return partial_match
        raise KeyError(full_idx)

    def _key_to_paths(self, key):
        if key[:3] in (b'id:', 'id:'):
            sub, p = self._find_by_idx(int(key[3:]))  # Raises KeyError?
            yield os.path.join(self.basedir, sub, p)

        else:
            for sub in (b'cur', b'new'):
                yield os.path.join(self.basedir, sub + key)

            # Since Maildirs keep metadata in the filename, we have to
            # consider that the filename might have changed since we last
            # looked. If we get this far we are still looking, so we scan
            # the directory for new matching names.
            key_basename = split_maildir_meta(key[1:])[0]
            for sub, fn in self.full_keys():
                if split_maildir_meta(fn)[0] == key_basename:
                    yield os.path.join(self.basedir, sub, fn)
                    self.needs_reindexing_cb(self)

    def __contains__(self, key):
        try:
            for p in self._key_to_paths(key):
                if p in self.parent:
                    return True
        except (OSError, ValueError, KeyError):
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
                    yield sub, fn

    def keys(self, skip=0):
        for sub, fn in self.full_keys(skip=skip):
            yield (self.sep + fn)

    def __iter__(self):
        return self.keys()

    def __len__(self):
        return sum(1 for s_f_i in self.full_keys())

    def unlock(self, username, password, ask_key=None, set_key=None):
        return self

    def get_email_headers(self, sub, fn):
        return self.parent[os.path.join(self.basedir, sub, fn)]

    def compare_idxs(self, idx1, idx2):
        (p1, h1) = unpack_maildir_idx(idx1)
        (p2, h2) = unpack_maildir_idx(idx2)
        return (h1 == h2)

    def iter_email_metadata(self, skip=0):
        lts = 0
        now = int(time.time())
        for i, (sub, fn) in enumerate(self.full_keys(skip=skip)):
            try:
                obj = self.get_email_headers(sub, fn)
                hend, hdrs = quick_msgparse(obj, 0)
                path = self.get_tagged_path(self.sep + fn)
                lts, md = make_ts_and_Metadata(
                    now, lts, obj[:hend],
                    [Metadata.PTR(Metadata.PTR.IS_FS, path, len(obj))],
                    hdrs)
                md[Metadata.OFS_IDX] = mk_maildir_idx(fn, i)
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

    def nr_cb(md):
        print('Detected that %s needs reindexing' % md)

    wanted1 = 'id:17493057840098'
    wanted2 = 'id:17493057839106'
    unwant3 = 'id:27493057840098'
    wanted4 = b'/1390872109_0.14842.slinky,U=69,FMD5=844bb96d088d057aa1b32ac1fbc67b56'
    for path in sys.argv[1:]:
        path = bytes(path, 'utf-8')
        md = FormatMaildir(fs, [path], None, needs_reindexing_cb=nr_cb)
        print('=== %s (%d) ===' % (path, len(md)))
        print('%s' % '\n'.join('%s' % m for m in md.iter_email_metadata()))
        for key in md.keys():
            assert(split_maildir_meta(key)[0] in md)
        for wanted in (wanted1, wanted2, unwant3, wanted4):
            if wanted in md:
                print('Found %s in mailbox!' % wanted)
        print('=== %s (%d) ===' % (path, len(md)))

