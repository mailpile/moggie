import mmap
import time
import os
import re

from ..email.metadata import Metadata
from ..util.dumbcode import *
from .base import BaseStorage


class FileMap(mmap.mmap):
    pass


class FileStorage(BaseStorage):

    def __init__(self, *args, **kwargs):
        self.relative_to = kwargs.get('relative_to')

        if 'relative_to' in kwargs:
            del kwargs['relative_to']
        if isinstance(self.relative_to, str):
            self.relative_to = self.relative_to.encode('utf-8')

        BaseStorage.__init__(self, *args, **kwargs)
        self.dict = None

    def relpath(self, path):
        if self.relative_to:
            return os.path.relpath(path, self.relative_to)
        else:
            return path

    def key_to_path(self, key):
        path = dumb_decode(key)
        if isinstance(path, str):
            path = path.encode('utf-8')
        if not isinstance(path, bytes):
            raise KeyError('Invalid key %s' % key)
        return path

    def __contains__(self, key):
        return os.path.exists(self.key_to_path(key))

    def __delitem__(self, key):
        return os.remove(self.key_to_path(key))

    def get_filemap(self, path, prefer_access=mmap.ACCESS_WRITE):
        try:
            with open(path, 'rb+') as fd:
                return FileMap(fd.fileno(), 0, access=prefer_access)
        except PermissionError:
            with open(path, 'rb') as fd:
                return FileMap(fd.fileno(), 0, access=mmap.ACCESS_READ)

    def __getitem__(self, key):
        try:
            return self.get_filemap(self.key_to_path(key))
        except IsADirectoryError:
            raise
        except OSError:
            pass
        raise KeyError('Not found or access denied for %s' % key)

    def __setitem__(self, key, value):
        with open(self.key_to_path(key), 'wb') as fd:
            fd.write(value)

    def append(self, key, value):
        with open(self.key_to_path(key), 'ab') as fd:
            fd.write(value)

    def length(self, key):
        return os.path.getsize(self.key_to_path(key))

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def dump(self):
        raise Exception('Not Implemented')

    def capabilities(self):
        return ['info', 'get', 'length', 'set']  #, 'del']

    def info(self, key=None, details=False, parse=False):
        path = self.key_to_path(key or b'B/')
        try:
            stat = os.stat(path)
        except OSError:
            return {'exists': False}

        is_dir = os.path.isdir(path)
        info = {
            'exists': True,
            'is_dir': is_dir,
            'size': stat.st_size,
            'mode': stat.st_mode,
            'owner': stat.st_uid,
            'group': stat.st_gid,
            'mtime': stat.st_mtime,
            'atime': stat.st_atime,
            'ctime': stat.st_ctime}

        if not details:
            return info

        if is_dir:
            info['contents'] = c = []
            for p in os.listdir(path):
                if p not in (b'.', b'..'):
                    c.append(dumb_encode_asc(os.path.join(path, p)))
            if ('Bnew' in c and 'Bcur' in c and 'Btmp' in c):
                info['magic'] = 'maildir'
        elif os.path.isfile(path):
            if self[key][:5] == b'From ':
                info['magic'] = 'mbox'

        if parse and key and 'magic' in info:
           try:
               if info['magic'] == 'mbox':
                   info['emails'] = list(self.parse_mbox(key))
               elif info['magic'] == 'maildir':
                   info['emails'] = list(self.parse_maildir(key))
           except TypeError:
               pass

        return info

    def quick_msgparse(self, obj, beg):
        sep = b'\r\n\r\n' if (b'\r\n' in obj[beg:beg+256]) else b'\n\n'

        hend = obj.find(sep, beg, beg+102400)
        if hend < 0:
            return None
        hend += len(sep)

        # Note: This is fast! We deliberately do not sort, as the order of
        #       headers is one of the things that makes messages unique.
        hdrs = (b'\n'.join(
                    h.strip()
                    for h in re.findall(Metadata.HEADER_RE, obj[beg:hend]))
            ).replace(b'\r', b'')

        return hend, hdrs

    def parse_mbox(self, key):
        path = self.key_to_path(key)
        relpath = self.relpath(path)
        obj = self.get_filemap(path, prefer_access=mmap.ACCESS_READ)
        beg = 0
        end = 0
        try:
         while end < len(obj):
            hend, hdrs = self.quick_msgparse(obj, beg)

            end = obj.find(b'\nFrom ', hend-1)
            if end < 0:
                end = len(obj)

            yield(Metadata(0, relpath, beg, hend-beg, end-beg, hdrs))
            beg = end+1
        except ValueError:
            return

    def parse_maildir(self, key):
        path = self.key_to_path(key or b'B/')
        for sd in (b'new', b'cur'):
            sd = os.path.join(path, sd)
            for fn in sorted(os.listdir(sd)):
                if fn.startswith(b'.'):
                    continue
                fn = os.path.join(sd, fn)
                with open(fn, 'rb') as fd:
                 with FileMap(fd.fileno(), 0, access=mmap.ACCESS_READ) as fm:
                    try:
                        hend, hdrs = self.quick_msgparse(fm, 0)
                        end = os.path.getsize(fn)
                        yield(Metadata(0, self.relpath(fn), 0, hend, end, hdrs))
                    except ValueError:
                        pass


if __name__ == "__main__":
    fs = FileStorage(relative_to=b'/home/bre')

    fn = dumb_encode_asc(__file__)
    assert(fs.length(fn) == len(fs[fn]))

    fs['B/tmp/test.txt'] = b'123456'
    fs.append('B/tmp/test.txt', b'12345')
    assert(bytes(fs['B/tmp/test.txt']) == b'12345612345')
    del fs['B/tmp/test.txt']

    print('Tests passed OK')

    print('%s\n\n' % fs.info('B/home/bre/Mail/GMaildir/[Gmail].All Mail', details=True, parse=True))
    print('%s\n\n' % fs.info('B/home/bre/Mail/mailpile/2013-08.mbx', details=True))

    big = 'B/home/bre/Mail/klaki/gmail-2011-11-26.mbx'
    print('%s\n\n' % fs.info(big, details=True))
    msgs = sorted(list(fs.parse_mbox(big)))
    print('Found %d messages in %s' % (len(msgs), big))
    for msg in msgs[:5] + msgs[-5:]:
        m = msg.parsed()
        f = m['from']
        print('%-38.38s %-40.40s' % (f.fn or f.address, m['subject']))

    for count in range(0, 5):
        i = count * (len(msgs) // 5)
        print('len(msgs[%d]) == %d' % (i, len(dumb_encode_bin(msgs[i], compress=256))))
    print('%s\n' % dumb_encode_bin(msgs[0], compress=None))

    try:
        print('%s' % fs['/tmp'])
    except IsADirectoryError:
        print('%s' % fs.info('/tmp', details=True))
    print('%s' % fs.info('/lskjdf', details=True))
