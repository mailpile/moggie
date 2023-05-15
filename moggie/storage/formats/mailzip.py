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
            return zipfile.AESZipFile(key, mode=mode)
        else:
            if isinstance(key, str):
                key = bytes(key, 'utf-8')
            return zipfile.AESZipFile(parent.key_to_path(key), mode=mode)

    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
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
        self.zf = self.Zipfile(parent, path[0], mode='r')
        password = kwargs.get('password')
        if password:
            self.unlock(None, password)

    def unlock(self, ignored_username, password, ask_key=None, set_key=None):
        if password:
            if not isinstance(password, bytes):
                password = bytes(password, 'utf-8')
            self.password = password
            self.zf.setpassword(password)
        return self

    def __contains__(self, key):
        return key[1:] in self.zf

    def __getitem__(self, key):
        if isinstance(key, bytes):
            key = str(key, 'utf-8')
        try:
            logging.debug('Reading: %s' % key[1:])
            with self.zf.open(key[1:], 'r') as fd:
                return fd.read()
        except RuntimeError as e:
            logging.debug('Oops: %s' % e)
            try:
                p = str(self.path[0], 'utf-8')
            except:
                p = self.path[0]
            raise PleaseUnlockError('Need password to decrypt %s (in %s)'
                    % (os.path.basename(p), os.path.dirname(p)),
                username=False,
                resource=self.path)

    def __delitem__(self, key):
        raise IOError('FIXME: Cannot delete from mailzips yet')

    def __iadd__(self, data):
        raise IOError('FIXME: Cannot add to mailzips yet')

    def append(self, data):
        raise IOError('FIXME: Cannot add to mailzips yet')

    def __setitem__(self, key, value):
        raise IOError('FIXME: Cannot add to mailzips yet')

    def keys(self):
        return sorted(['/' + i.filename
            for i in self.zf.infolist() if self.FILE_RE.search(i.filename)])

    def iter_email_metadata(self,
            skip=0, iterator=None, username=None, password=None):
        now = int(time.time())
        lts = 0
        obj = ''
        try:
            for key in self.keys():
                obj = self[key]
                path = self.get_tagged_path(bytes(key, 'utf-8'))
                hend, hdrs = quick_msgparse(obj, 0)
                lts, md = make_ts_and_Metadata(
                    now, lts, obj[:hend],
                    Metadata.PTR(Metadata.PTR.IS_FS, path, len(obj)),
                    hdrs)
                yield(md)
        except (KeyError, ValueError, TypeError) as e:
            logging.exception('Failed to read mailbox')
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

