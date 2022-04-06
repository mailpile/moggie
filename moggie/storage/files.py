import email.utils
import mmap
import time
import threading
import os
import re

from ..email.metadata import Metadata
from ..email.headers import parse_header
from ..email.parsemime import parse_message as ep_parse_message
from ..util.dumbcode import *
from .base import BaseStorage


class FileMap(mmap.mmap):
    pass


class FileStorage(BaseStorage):

    EMAIL_PTR_TYPES = (Metadata.PTR.IS_MBOX, Metadata.PTR.IS_MAILDIR)

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
        if self.relative_to and not path.startswith(self.relative_to):
            return os.path.join(self.relative_to, path)
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

    def info(self, key=None, details=False, limit=None, skip=0):
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
            'mtime': int(stat.st_mtime),
            'atime': int(stat.st_atime),
            'ctime': int(stat.st_ctime)}

        if not details:
            return info

        if is_dir:
            info['contents'] = c = []
            maildir = 0
            for p in os.listdir(path):
                if p not in (b'.', b'..'):
                    c.append(dumb_encode_asc(os.path.join(path, p)))
                if p in (b'new', b'cur', b'tmp'):
                    maildir += 1
            if maildir == 3:
                info['magic'] = 'maildir'
        elif os.path.isfile(path):
            if self[key][:5] == b'From ':
                info['magic'] = 'mbox'

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

    def parse_mailbox(self, key, skip=0, limit=None):
        path = self.key_to_path(key)

        if (limit is not None) and limit <= 0:
            parser = iter([])
        elif os.path.isfile(path) and self[key][:5] == b'From ':
            parser = self.parse_mbox(key, skip=skip)
        elif os.path.isdir(os.path.join(path, b'cur')):
            parser = self.parse_maildir(key, skip=skip)
        else:
            parser = iter([])

        if limit is None:
            yield from parser
        else:
            for msg in parser:
                yield msg
                limit -= 1
                if limit <= 0:
                    break

    def _ts_and_Metadata(self, now, lts, raw_headers, *args):
        # Extract basic metadata. If we fail to find a plausible timestamp,
        # try harder and then make one up that seems plausible, based on the
        # assumption that messages are in chronological order in the mailbox.
        md = Metadata(0, 0, *args)
        if md.timestamp and (md.timestamp > lts/2) and (md.timestamp < now):
            return (max(lts, md.timestamp), md)

        md[md.OFS_TIMESTAMP] = lts

        # Could not parse Date - do we have a From line with a date?
        raw_headers = str(raw_headers, 'latin-1')
        if raw_headers[:5] == 'From ':
            dt = raw_headers.split('\n', 1)[0].split('  ', 1)[-1].strip()
            try:
                ts = int(time.mktime(email.utils.parsedate(dt)))
                md[md.OFS_TIMESTAMP] = ts
                return (max(lts, md.timestamp), md)
            except (ValueError, TypeError):
                pass

        # Fall back to scanning the Received headers
        rcvd_ts = []
        for rcvd in parse_header(raw_headers).get('received', []):
            try:
                tail = rcvd.split(';')[-1].strip()
                rcvd_ts.append(int(time.mktime(email.utils.parsedate(tail))))
            except (ValueError, TypeError):
                pass
        if rcvd_ts:
            rcvd_ts.sort()
            md[md.OFS_TIMESTAMP] = rcvd_ts[len(rcvd_ts) // 2]

        return (max(lts, md.timestamp), md)

    def parse_mbox(self, key, skip=0):
        path = self.key_to_path(key)
        relpath = self.relpath(path)
        obj = self.get_filemap(path, prefer_access=mmap.ACCESS_READ)
        beg = 0
        end = 0
        lts = 0
        now = int(time.time())
        try:
          while end < len(obj):
            hend, hdrs = self.quick_msgparse(obj, beg)

            end = obj.find(b'\nFrom ', hend-1)
            if end < 0:
                end = len(obj)

            if skip > 0:
                skip -= 1
            else:
                hl, ml = hend-beg, end-beg
                lts, md = self._ts_and_Metadata(
                    now, lts, obj[beg:hend],
                    [Metadata.PTR(Metadata.PTR.IS_MBOX, relpath, beg, hl, ml)],
                    hdrs)
                yield(md)

            beg = end+1
        except (ValueError, TypeError):
            return

    def parse_maildir(self, key, skip=0):
        path = self.key_to_path(key or b'b/')
        lts = 0
        now = int(time.time())
        for sd in (b'new', b'cur'):
            sd = os.path.join(path, sd)

            # FIXME: For very large maildirs, this os.listdir() call can
            #        be quite costly. We *might* want to cache the result.
            for fn in sorted(os.listdir(sd)):
                if fn.startswith(b'.'):
                    continue
                if skip > 0:
                    skip -= 1
                    continue

                fn = os.path.join(sd, fn)
                with open(fn, 'rb') as fd:
                  with FileMap(fd.fileno(), 0, access=mmap.ACCESS_READ) as fm:
                    try:
                        hend, hdrs = self.quick_msgparse(fm, 0)
                        end = os.path.getsize(fn)

                        lts, md = self._ts_and_Metadata(
                            now, lts, fm[:hend],
                            [Metadata.PTR(Metadata.PTR.IS_MAILDIR,
                                          self.relpath(fn), 0, hend, end)],
                            hdrs)
                        yield(md)
                    except (ValueError, TypeError):
                        pass

    def message(self, metadata, with_ptr=False):
        """
        Returns a slice of bytes that map to the message on disk.
        Works for both maildir and mbox messages.
        """
        ptr = metadata.pointers[0]  # Filesystem pointers are always first
        if ptr.ptr_type not in self.EMAIL_PTR_TYPES:
            raise KeyError('Not a filesystem pointer: %s' % ptr)
        beg = ptr.offset
        end = ptr.offset + ptr.message_length
        if with_ptr:
            return ptr, self[ptr.mailbox][beg:end]
        else:
            return self[ptr.mailbox][beg:end]

    def parse_message(self, metadata):
        ptr, msg = self.message(metadata, with_ptr=True)
        return ep_parse_message(msg,
            fix_mbox_from=(ptr.ptr_type == Metadata.PTR.IS_MBOX))


if __name__ == "__main__":
    import sys

    fs = FileStorage(relative_to=b'/home/bre')
    fn = dumb_encode_asc(__file__)
    assert(fs.length(fn) == len(fs[fn]))

    fs['B/tmp/test.txt'] = b'123456'
    fs.append('B/tmp/test.txt', b'12345')
    assert(bytes(fs['B/tmp/test.txt']) == b'12345612345')
    del fs['B/tmp/test.txt']

    print('Tests passed OK')
    if 'more' in sys.argv:
        print('%s\n\n' % fs.info('b/home/bre/Mail/GMaildir/[Gmail].All Mail', details=True))
        print('%s\n\n' % fs.info('b/home/bre/Mail/mailpile/2013-08.mbx', details=True))

        big = 'b/home/bre/Mail/klaki/gmail-2011-11-26.mbx'
        print('%s\n\n' % fs.info(big, details=True))
        msgs = sorted(list(fs.parse_mailbox(big)))
        print('Found %d messages in %s' % (len(msgs), big))
        for msg in msgs[:5] + msgs[-5:]:
            m = msg.parsed()
            f = m['from']
            print('%-38.38s %-40.40s' % (f.fn or f.address, m['subject']))

        for count in range(0, 5):
            i = count * (len(msgs) // 5)
            print('len(msgs[%d]) == %d' % (i, len(dumb_encode_bin(msgs[i], compress=256))))
        print('%s\n' % dumb_encode_bin(msgs[0], compress=None))

        import json, random
        print(json.dumps(
            fs.parse_message(random.choice(msgs)).with_text().with_data(),
            indent=2))

        try:
            print('%s' % fs['/tmp'])
        except IsADirectoryError:
            print('%s' % fs.info('/tmp', details=True))
        print('%s' % fs.info('/lskjdf', details=True))
