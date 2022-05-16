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
        return ['info', 'get', 'length', 'set', 'del']

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


class MailboxFileStorage(FileStorage):
    """
    This extends the basic FileStorage with mbox and Maildir handling
    features.
    """
    EMAIL_PTR_TYPES = (Metadata.PTR.IS_MBOX, Metadata.PTR.IS_MAILDIR)

    DELETED_MARKER = b"""\
From DELETED\r\n\
From: nobody <deleted@example.org>\r\n\
\r\n\
(deleted)\r\n"""
    DELETED_FILLER = b"                                                    \r\n"

    def __init__(self, *args, **kwargs):
        self.metadata = kwargs.get('metadata')
        if 'metadata' in kwargs:
            del kwargs['metadata']

        # FIXME: We should compact mboxes periodically, to reclaim space.
        #        This is an operation we need to coordinate with the metadata
        #        index, since it changes the pointers for such messages.
        self.needs_compacting = set()

        FileStorage.__init__(self, *args, **kwargs)

    def capabilities(self):
        return super().capabilities() + ['mailboxes']

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
        delmark = self.DELETED_MARKER
        needs_compacting = 0
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
                    if obj[beg:beg+len(delmark)] == delmark:
                        needs_compacting += 1
                    else:
                        lts, md = self._ts_and_Metadata(
                            now, lts, obj[beg:hend],
                            [Metadata.PTR(
                                Metadata.PTR.IS_MBOX, relpath, beg, hl, ml)],
                            hdrs)
                        yield(md)

                beg = end+1
        except (ValueError, TypeError):
            return
        finally:
            if needs_compacting:
                self.needs_compacting.add(relpath)

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

    def add_message(self, mailbox, message):
        """
        Add a message to a mailbox.
        """
        raise Exception('FIXME')

    def delete_message(self, metadata=None, ptrs=None):
        """
        Delete the message from one or more locations.
        Returns a list of pointers which could not be deleted.
        """
        failed = []
        for ptr in (ptrs if (ptrs is not None) else metadata.pointers):
            if ptr.ptr_type == Metadata.PTR.IS_MBOX:
                length = ptr.message_length
                beg = ptr.offset
                end = ptr.offset + length
                fill = (
                    self.DELETED_MARKER +
                    self.DELETED_FILLER * (1 + length // len(self.DELETED_FILLER))
                    )[:length-2] + b'\r\n'
                self[ptr.mailbox][beg:end] = fill
                self.needs_compacting.add(ptr.mailbox)
            elif ptr.ptr_type == Metadata.PTR.IS_MAILDIR:
                del self[ptr.mailbox]
            else:
                failed.append(ptr)
        return failed

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

        # FIXME: We need to check whether this is actually the right message, or
        #        whether the mailbox has changed from under us. If it has, we
        #        need to (in coordination with the metadata index) rescan for
        #        messages update the metadata. This is true for both mbox and
        #        Maildir: Maildir files may get renamed if other apps change
        #        read/unread status or assign tags. For mbox, messages can move
        #        around within the file.

        data = self[ptr.mailbox][beg:end]
        if with_ptr:
            return ptr, data
        else:
            return data

    def parse_message(self, metadata):
        ptr, msg = self.message(metadata, with_ptr=True)
        return ep_parse_message(msg,
            fix_mbox_from=(ptr.ptr_type == Metadata.PTR.IS_MBOX))


if __name__ == "__main__":
    import sys

    fs = MailboxFileStorage(relative_to=b'/home/bre')
    fn = dumb_encode_asc(__file__)
    assert(fs.length(fn) == len(fs[fn]))

    fs['B/tmp/test.txt'] = b'123456'
    fs.append('B/tmp/test.txt', b'12345')
    assert(bytes(fs['B/tmp/test.txt']) == b'12345612345')
    del fs['B/tmp/test.txt']

    print('Tests passed OK')
    if 'more' in sys.argv:
        tmbox = '/tmp/test.mbx'
        os.system('cp /home/bre/Mail/mailpile/2013-08.mbx '+tmbox)
        print('%s\n\n' % fs.info('b/home/bre/Mail/GMaildir/[Gmail].All Mail', details=True))
        print('%s\n\n' % fs.info('b'+tmbox, details=True))

        msgs1 = sorted(list(fs.parse_mbox('b'+tmbox)))
        assert([] == fs.delete_message(msgs1[0]))
        msgs2 = sorted(list(fs.parse_mbox('b'+tmbox)))
        assert(len(msgs1) == len(msgs2)+1)
        os.remove(tmbox)

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
