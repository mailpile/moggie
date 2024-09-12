import logging
import mmap
import time
import threading
import os

from collections import OrderedDict

from ..email.metadata import Metadata
from ..util.dumbcode import *
from ..util.mailpile import PleaseUnlockError

from .base import BaseStorage
from .formats import split_tagged_path, tag_path
from .formats.base import FormatBytes
from .formats.mbox import FormatMbox
from .formats.maildir import FormatMaildir
from .formats.mailzip import FormatMailzip
from .formats.mailpilev1 import FormatMaildirWERVD
from .mailboxes import MailboxStorageMixin


# These are the file types we understand how to parse. Note that the
# order matters, the first match will be used in case there might be
# multiple.
FORMATS = OrderedDict()
FORMATS[FormatMbox.TAG] = FormatMbox
FORMATS[FormatMaildirWERVD.TAG] = FormatMaildirWERVD
FORMATS[FormatMaildir.TAG] = FormatMaildir
FORMATS[FormatMailzip.TAG] = FormatMailzip
FORMATS[FormatBytes.TAG] = FormatBytes


# Keep track globally of mailboxes which want us to compact them
NEEDS_COMPACTING = set()


class FileMap(mmap.mmap):
    pass


class FileStorage(BaseStorage, MailboxStorageMixin):
    def __init__(self,
            relative_to=None, metadata=None,
            ask_secret=None, set_secret=None):
        self.metadata = metadata
        self.relative_to = relative_to
        self.ask_secret = ask_secret
        self.set_secret = set_secret
        if isinstance(self.relative_to, str):
            self.relative_to = self.relative_to.encode('utf-8')

        super().__init__()
        self.dict = None

    @classmethod
    def RegisterFormat(cls, fmt):
        FORMATS[fmt.TAG] = fmt

    def relpath(self, path):
        if self.relative_to:
            return os.path.relpath(path, self.relative_to)
        else:
            return path

    def key_to_paths(self, key):
        path = dumb_decode(key)
        if isinstance(path, str):
            path = path.encode('utf-8')
        if not isinstance(path, bytes):
            raise KeyError('Invalid key %s' % key)
        if self.relative_to and not path.startswith(self.relative_to):
            path = os.path.join(self.relative_to, path)

        return split_tagged_path(path)

    def key_to_path(self, key):
        return self.key_to_paths(key)[0]

    def __contains__(self, key):
        paths = self.key_to_paths(key)
        filepath = paths.pop(0)
        if not os.path.exists(filepath):
            return False
        if paths:
            try:
                val = self.__getitem__(key)
            except:
                return False
        return True

    def __delitem__(self, key, *unlock_args):
        paths = self.key_to_paths(key)
        ptr = [paths.pop(0)]
        if not paths:
            return os.remove(ptr[0])
        else:
            try:
                cc = self.get_filemap(ptr[0])
            except IsADirectoryError:
                cc = None
            for sub_type, sub_path in paths:
                cd = FORMATS[sub_type](self, ptr, cc)
                if unlock_args:
                    logging.debug('unlock_args=%s' % (unlock_args,))
                    cd = self.unlock_mailbox(cd, *unlock_args)
                cc = cd[sub_path]
                ptr.append((sub_type, sub_path))
            del cd[sub_path]

    def get_filemap(self, path, prefer_access=mmap.ACCESS_WRITE):
        try:
            try:
                with open(path, 'rb+') as fd:
                    return FileMap(fd.fileno(), 0, access=prefer_access)
            except PermissionError:
                with open(path, 'rb') as fd:
                    return FileMap(fd.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError as e:
            return b''  # mmap() thows ValueError on empty file

    def __getitem__(self, key, *unlock_args):
        try:
            paths = self.key_to_paths(key)
            ptr = [paths.pop(0)]
            try:
                cc = self.get_filemap(ptr[0])
            except IsADirectoryError:
                cc = None
            for sub_type, sub_path in paths:
                sc = FORMATS[sub_type](self, ptr, cc)
                if unlock_args:
                    logging.debug('unlock_args=%s' % (unlock_args,))
                    sc = self.unlock_mailbox(sc, *unlock_args)
                cc = sc[sub_path]
                ptr.append((sub_type, sub_path))
            return cc
        except PleaseUnlockError:
            raise
        except OSError:
            pass
        raise KeyError('Not found or access denied for %s' % key)

    def __setitem__(self, key, value):
        paths = self.key_to_paths(key)
        filepath = paths.pop(0)
        ptr = [filepath]
        if not paths:
            with open(filepath, 'wb') as fd:
                fd.write(value)
        else:
            try:
                cc = self.get_filemap(ptr[0])
            except IsADirectoryError:
                cc = None
            for sub_type, sub_path in paths:
                cd = FORMATS[sub_type](self, ptr, cc)
                cc = cd[sub_path]
                ptr.append((sub_type, sub_path))
            cd[sub_path] = value

    def append(self, key, value):
        paths = self.key_to_paths(key)
        filepath = paths.pop(0)
        if paths:
            raise IndexError('Cannot append to subpaths')
        else:
            with open(filepath, 'ab') as fd:
                fd.write(value)

    def rename(self, src, dst):
        sps, dps = self.key_to_paths(src), self.key_to_paths(dst)
        src, dst = sps.pop(0), dps.pop(0)
        if sps or dps:
            raise ValueError('Can only rename untagged paths')
        return os.rename(src, dst)

    def length(self, key):
        paths = self.key_to_paths(key)
        filepath = paths.pop(0)
        if not paths:
            return os.path.getsize(filepath)
        else:
            return len(self[key])

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def dump(self):
        raise Exception('Not Implemented')

    def listdir(self, key):
        try:
            for p in os.listdir(self.key_to_path(key)):
                if p not in (b'.', b'..'):
                    yield p
        except:
            pass

    def info(self, key=None,
            details=False, recurse=None, relpath=None,
            username=None, password=None,
            limit=None, skip=0):
        userhome = os.path.expanduser(b'~')
        paths = self.key_to_paths(key)
        path = paths.pop(0)
        try:
            if paths:
                return {
                    'size': len(self[key]),
                    'path': path,
                    'sub_paths': paths}
            else:
                stat = os.stat(path)
        except (OSError, KeyError, IndexError, ValueError):
            return {'path': path, 'exists': False}

        def _utf8(t):
            try:
                return str(t, 'utf-8')
            except UnicodeDecodeError:
                return t

        # Add source info, just like config.paths.mail_path_suggestions
        src = ('home'
            if (path.startswith(userhome+b'/') or path == userhome)
            else '')
        if src == 'home':
            if b'/share/Mailpile/' in path or b'/.mailpile/' in path:
                src = 'mailpilev1'
            elif path.startswith(os.path.expanduser(b'~/.thunderbird/')):
                src = 'thunderbird'
        elif path.startswith(b'/var/'):
            src = 'spool'
        else:
            src = 'fs'

        is_dir = os.path.isdir(path)
        info = {
            'src': src,
            'path': _utf8(path),
            'exists': True,
            'is_dir': is_dir,
            'size': stat.st_size,
            'mode': stat.st_mode,
            'owner': stat.st_uid,
            'group': stat.st_gid,
            'mtime': int(stat.st_mtime)}

        if not details:
            return info

        if relpath is None:
            relpath = True

        if is_dir and (details is True or 'contents' in details):
            c = []
            rp = self.relpath(path) if relpath else path
            for p in self.listdir(key):
                subpath = os.path.join(rp, p)
                if recurse:
                    rec_next = max(0, recurse - 1)
                    det_next = True # 'magic' if (not rec_next) else True
                    c.append(self.info(subpath,
                        details=det_next, relpath=relpath, recurse=rec_next,
                        username=username, password=password))
                else:
                    c.append(_utf8(subpath))
                if (len(c) > 0) and (recurse == 0):
                    break
            info['has_children'] = (len(c) > 0)
            if (recurse != 0):
                info['contents'] = c

        if details is True or 'magic' in details:
            magic = []
            for cls_type, cls in FORMATS.items():
                if cls.Magic(self, path, is_dir=is_dir):
                    magic.append(cls.NAME)
            if magic:
                info['magic'] = magic

        return info

    def need_compacting(self, path):
        NEEDS_COMPACTING.add(path)

    def get_mailbox(self, key, auth=None):
        paths = self.key_to_paths(key)
        filepath = paths[0]
        if len(paths) > 1:
            raise ValueError('Cannot currently handle nested tagging')
        for cls_type, cls in FORMATS.items():
            if hasattr(cls, 'iter_email_metadata'):
                if cls.Magic(self, filepath, is_dir=os.path.isdir(filepath)):
                    return cls(self, paths, self[filepath])
        return None

    def can_handle_ptr(self, ptr):
        return (ptr.ptr_type == Metadata.PTR.IS_FS)


if __name__ == "__main__":
    import sys

    tags = [b'/hello/world', (b'csv', b'@1,2')]
    tpath = tag_path(*tags)
    assert(tpath == b'/hello/world@1,2[csv:4]')
    assert(split_tagged_path(tpath) == tags)
    assert(split_tagged_path(b'/ohai[:0]') == [b'/ohai'])
    assert(tag_path(b'/a[]') == b'/a[][:0]')
    assert(tag_path(b'/a[]b') == b'/a[]b')
    assert(split_tagged_path(tag_path(b'/a[]'))[0] == b'/a[]')
    assert(split_tagged_path(tag_path(b'/a[]b'))[0] == b'/a[]b')

    fs = FileStorage(relative_to=b'/home/bre')
    assert(fs.key_to_paths('b/tmp/test.txt') == [b'/tmp/test.txt'])
    assert(fs.key_to_paths('b/tmp/test.txt>1-2[b:4]')
        == [b'/tmp/test.txt', (b'b', b'>1-2')])

    fn = dumb_encode_asc(__file__)
    assert(fs.length(fn) == len(fs[fn]))

    fs['b/tmp/test.txt'] = b'123456'
    fs.append('b/tmp/test.txt', b'12345')
    assert(bytes(fs['b/tmp/test.txt']) == b'12345612345')
    del fs['b/tmp/test.txt']

    print('Tests passed OK')
    if 'more' in sys.argv:
        tmbox = '/tmp/test.mbx'
        os.system('cp /home/bre/Mail/mailpile/2013-08.mbx '+tmbox)
        print('%s\n' % fs.info(b'/home/bre/Mail/GMaildir/[Gmail].All Mail', details=True))
        print('%s\n' % fs.info(tmbox, details=True))

        msgs1 = sorted(list(fs.iter_mailbox('b'+tmbox)))
        assert([] == fs.delete_message(msgs1[0]))
        msgs2 = sorted(list(fs.iter_mailbox('b'+tmbox)))
        assert(len(msgs1) == len(msgs2)+1)
        os.remove(tmbox)

        big = 'b/home/bre/Mail/klaki/gmail-2011-11-26.mbx'
        print('%s\n\n' % fs.info(big, details=True))
        msgs = sorted(list(fs.iter_mailbox(big)))
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
