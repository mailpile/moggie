import logging
import os
import re
import time
import pyzipper as zipfile

from ...email.metadata import Metadata
from ...email.headers import parse_header
from ...email.util import quick_msgparse, make_ts_and_Metadata
from ...util.mailpile import PleaseUnlockError

from . import tag_path
from .base import FormatBytes


COUNTER = 0


class FormatMailzip(FormatBytes):
    NAME = 'mailzip'
    TAG = b'mz'

    FILE_RE = re.compile(r'(^|/)(cur|new)/[^/]+[:;-]2,[^/]*$')

    @classmethod
    def Zipfile(self, parent, key, mode='r'):
        if hasattr(key, 'fileno'):
            return zipfile.ZipFile(key, mode=mode)
        else:
            if isinstance(key, str):
                key = bytes(key, 'utf-8')
            return zipfile.ZipFile(parent.key_to_path(key), mode=mode)

    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
        print('Magic(%s, %s)' % (parent, key))
        if is_dir:
            return False
        try:
            with cls.Zipfile(parent, key) as zf:
                for name in zf.namelist():
                    if cls.FILE_RE.search(name):
                        return True
            return False
        except:
            raise
            return False

    def __init__(self, parent, path, container, **kwargs):
        super().__init__(parent, path, container, **kwargs)
        # FIXME: Crypto!
        self.zf = self.Zipfile(parent, path[0], mode='r')

    def __contains__(self, key):
        return key in self.files

    def __getitem__(self, key):
        if isinstance(key, bytes):
            key = str(key, 'utf-8')
        with self.zf.open(key, 'r') as fd:
            return fd.read()

    def __delitem__(self, key):
        raise IOError('FIXME: Cannot delete from mailzips yet')

    def __iadd__(self, data):
        raise IOError('FIXME: Cannot add to mailzips yet')

    def append(self, data):
        raise IOError('FIXME: Cannot add to mailzips yet')

    def __setitem__(self, key, value):
        raise IOError('FIXME: Cannot add to mailzips yet')

    def keys(self):
        return sorted([i.filename
            for i in self.zf.infolist() if self.FILE_RE.search(i.filename)])

    def iter_email_metadata(self,
            skip=0, iterator=None, username=None, password=None):
        now = int(time.time())
        lts = 0
        try:
            for key in self.keys():
                path = self.get_tagged_path(bytes(key, 'utf-8'))
                obj = self[key]
                hend, hdrs = quick_msgparse(obj, 0)
                lts, md = make_ts_and_Metadata(
                    now, lts, obj[:hend],
                    Metadata.PTR(Metadata.PTR.IS_FS, path, len(obj)),
                    hdrs)
                yield(md)
        except (KeyError, ValueError, TypeError) as e:
            import traceback
            traceback.print_exc()
            return


if __name__ == "__main__":
    import os, sys
    from ..files import FileStorage

    fs = FileStorage()
    fs.RegisterFormat(FormatMailzip)

    for path in sys.argv[1:]:
        path = bytes(os.path.abspath(path), 'utf-8')
        assert(FormatMailzip.Magic(fs, path, None, is_dir=False))
        md = FormatMailzip(fs, [path], None)
        print('=== %s (%d) ===' % (path, len(md)))
        print('%s' % '\n'.join(md.keys()))
        print('%s' % '\n'.join('%s' % m for m in md.iter_email_metadata()))
        print('=== %s (%d) ===' % (path, len(md)))

