import copy
import datetime
import io
import logging
import os
import tarfile
import time

try:
    import pyzipper as zipfile
    HAVE_ZIP_AES = True
except ImportError:
    import zipfile
    HAVE_ZIP_AES = False

from moggie.email.sync import generate_sync_fn_part

from .base import *
from .mbox import MboxExporter


ENCRYPTED_README = """\
This archive was generated using moggie, an Open Source tool for searching and
managing e-mail. The contents are encrypted using WinZip-compatible 128-bit AES
encryption. You will need a password!

Please use WinZip, 7-Zip or the Mac Archive Utility to access the contents.
"""


class DirWriter:
    pass


class ZipWriter:
    CAN_ENCRYPT = HAVE_ZIP_AES
    CAN_DELETE = True

    def __init__(self, fd, d_acl=0o040750, f_acl=0o000640, password=None):
        self.zf = self._open_or_create(fd, password)
        self.should_compact = False
        self.encrypting = (password is not None)

        # This kludge keeps us compatible with both pyzipper and zipfile
        self.zipinfo_cls = zipfile.ZipInfo
        if hasattr(self.zf, 'zipinfo_cls'):
            self.zipinfo_cls = self.zf.zipinfo_cls

        self.d_acl = d_acl
        self.f_acl = f_acl
        if password:
            self.add_file('README.txt', time.time(), ENCRYPTED_README,
                encrypt=False)

    def _open_or_create(self, fd, password):
        mode = 'w'
        if isinstance(fd, (str, bytes)):
            try:
                fd = open(fd, 'r+b')
                mode = 'a'
            except OSError:
                fd = open(fd, 'w+b')
        else:
            try:
                cur = fd.tell()
                fd.seek(0, 2)
                if fd.tell() != cur:
                    fd.seek(cur, 0)
                    mode = 'a'
            except (AttributeError, IOError, OSError):
                pass
        if self.CAN_ENCRYPT:
            zf = zipfile.AESZipFile(fd,
                compression=zipfile.ZIP_DEFLATED,
                mode=mode)
            if password:
                zf.setpassword(password)
                zf.setencryption(zipfile.WZ_AES, nbits=256)
            return zf
        else:
            if password:
                logging.error('ZIP encryption is unavailable')
                raise IOError('ZIP encryption is unavailable')
            return zipfile.ZipFile(fd, mode=mode)

    def _tt(self, ts):
        if self.encrypting:
            ts -= (ts % 3600)
        return datetime.datetime.fromtimestamp(ts).timetuple()

    def mkdir(self, dn, ts):
        dn = dn if (dn[-1:] == '/') else (dn + '/')
        if dn not in self.zf.namelist():
            dirent = self.zipinfo_cls(filename=dn, date_time=self._tt(ts))
            dirent.compress_type = zipfile.ZIP_STORED
            dirent.external_attr = self.d_acl << 16  # Unix permissions
            dirent.external_attr |= 0x10             # MS-DOS directory flag
            self.zf.writestr(dirent, b'')

    def delete_file(self, filename):
        try:
            logging.debug('Deleting %s' % fn)
            self.zf.delete(fn)
            self.should_compact = True
        except AttributeError:
            logging.error('ZIP deletion unavailable, skipping %s' % fn)
            raise IOError('Deletion is unavailable')

    def delete_by_prefix(self, fn_prefix):
        try:
            for fn in self.zf.namelist():
                if fn.startswith(fn_prefix):
                    logging.debug('Deleting %s' % fn)
                    self.zf.delete(fn)
                    self.should_compact = True
        except AttributeError:
            logging.error('ZIP deletion unavailable, skipping %s' % fn)
            raise IOError('Deletion is unavailable')

    def add_file(self, fn, ts, data, encrypt=True):
        logging.debug('Adding %s' % fn)
        if fn in self.zf.namelist():
            try:
                self.zf.delete(fn)
            except AttributeError:
                logging.warn('ZIP overwriting unavailable, skipping %s' % fn)
                return
        fi = self.zipinfo_cls(filename=fn, date_time=self._tt(ts))
        fi.external_attr = self.f_acl << 16
        fi.compress_type = zipfile.ZIP_DEFLATED
        if encrypt:
            self.zf.writestr(fi, data)
        else:
            self.zf.writestr(fi, data, encrypt=False)

    def compact(self):
        try:
            self.zf.compact()
            self.should_compact = False
        except AttributeError:
            pass

    def close(self):
        self.zf.close()


class TarWriter(ZipWriter):
    CAN_ENCRYPT = False
    CAN_DELETE = False

    def _open_or_create(self, fd, password):
        if password:
            raise OSError('Encrypted Tar files are not implemented')
        if isinstance(fd, (str, bytes)):
            zm = 'w'
            ext = _ext(fd)
            if ext in (b'gz', b'tgz'):
                zm += ':gz'
            elif ext == b'xz':
                zm += ':xz'
            elif ext == b'bz2':
                zm += ':bz2'
            return tarfile.open(name=fd, mode=zm)
        else:
            return tarfile.open(fileobj=fd, mode='w:gz')

    def mkdir(self, dn, ts):
        dn = dn if (dn[-1:] == '/') else (dn + '/')
        dirent = tarfile.TarInfo(name=dn)
        dirent.type = tarfile.DIRTYPE
        dirent.size = 0
        dirent.mtime = ts
        dirent.mode = self.d_acl
        dirent.uname = 'mailpile'
        dirent.gname = 'mailpile'
        self.zf.addfile(dirent)

    def add_file(self, fn, ts, data):
        fi = tarfile.TarInfo(name=fn)
        fi.type = tarfile.REGTYPE
        fi.size = len(data)
        fi.mtime = ts
        fi.mode = self.f_acl
        fi.uname = 'mailpile'
        fi.gname = 'mailpile'
        self.zf.addfile(fi, io.BytesIO(data))

    def delete_by_prefix(self, fn_prefix):
        logging.error('Deletion unavailable, skipping %s' % filename)
        raise IOError('Deletion is unavailable')

    def delete_file(self, filename):
        logging.error('Deletion unavailable, skipping %s' % filename)
        raise IOError('Deletion is unavailable')

    def compact(self):
        pass


def _ext(fn):
    fn = bytes(fn, 'utf-8') if isinstance(fn, str) else fn
    return fn.rsplit(b'.')[-1]


class MaildirExporter(BaseExporter):
    """
    Export messages as a zipped or tarred Maildir, generating filenames
    which include our read/unread status and match our internal
    metadata/tags.
    """
    AS_ZIP = 0
    AS_TAR = 1
    AS_DIR = 2
    AS_DEFAULT = AS_TAR
    SUBDIRS = ('cur', 'new', 'tmp')

    PREFIX = 'cur/'
    SUFFIX = ''
    FMT_DIRNAME = 'maildir.%x'
    # FIXME: The taglist should come AFTER the separator, as tags change
    #        Check the maildir spec!
    FMT_FILENAME = '%(dir)s%(prefix)smoggie%(sync_fn)st=%(tags)s%(flags)s%(suffix)s'

    MANGLE_ADD_FROM = False
    MANGLE_ADD_HEADERS = False
    MANGLE_FIX_EOL = b'\n'
    BASIC_FLAGS = {
        'forwarded': 'P',
        'bounced':   'P',
        'resent':    'P',
        'replied':   'R',
        'seen':      'S',
        'drafts':    'D',
        'flagged':   'F',
        'trash':     'T'}

    def __init__(self, real_fd,
            dirname=None, output=None, eol=None,
            password=None, moggie_id=None, src=None, dest=None):
        self.eol = self.MANGLE_FIX_EOL if (eol is None) else eol

        if output is None:
            # No output specified, check we have a filename/dirname that
            # tells us what the user wants instead.
            output = self.AS_DEFAULT
            if isinstance(real_fd, (str, bytes)):
                ext = _ext(real_fd)
                if os.path.isdir(real_fd):
                    output = self.AS_DIR
                elif ext in (b'gz', b'tar', b'tgz', b'bz2', b'xz'):
                    output = self.AS_TAR
                elif ext in (b'zip', 'mdz'):
                    output = self.AS_ZIP
                elif real_fd[-1] in (b'/', '/'):
                    output = self.AS_DIR

        if output == self.AS_TAR:
            ocls = TarWriter
            self.sep = ':'
        elif output == self.AS_ZIP:
            ocls = ZipWriter
            self.sep = ';'
        else:
            ocls = DirWriter
            self.sep = ';'  # FIXME: If dir exists, check what it uses?

        now = int(time.time())
        self.real_fd = real_fd
        self.writer = ocls(real_fd, password=password)

        if dirname is None:
            dirname = self.default_basedir(dest)
        self.basedir = dirname

        if self.basedir:
            self.writer.mkdir(self.basedir, now)
            self.basedir += '/'
        for sub in self.SUBDIRS:
            self.writer.mkdir(self.basedir + sub, now)

        super().__init__(self.writer,
            password=password, moggie_id=moggie_id, src=src, dest=dest)

    def can_encrypt(self):
        return self.writer.CAN_ENCRYPT

    def default_basedir(self, dest):
        if dest:
            dest = dest.rstrip('/')
        if dest:
            dparts = os.path.basename(dest).rsplit('.')
            while dparts and dparts[-1] in ('tar', 'xz', 'bz2', 'gz', 'tgz', 'zip', 'mdz'):
                dparts.pop(-1)
            dest = '.'.join(dparts)
            if dest:
                return dest
        return self.FMT_DIRNAME % int(time.time())

    def flags(self, tags):
        flags = set()
        if 'read' in tags:
            flags.add(self.BASIC_FLAGS['seen'])
        for tag in tags:
            if tag in self.BASIC_FLAGS:
                flags.add(self.BASIC_FLAGS[tag])
        return '%s2,%s' % (self.sep, ''.join(sorted([f for f in flags])))

    def get_filename(self, metadata):
        ts = metadata.timestamp
        idx = metadata.idx
        tags = [t.split(':')[-1] for t in metadata.more.get('tags', [])]
        taglist = ','.join(tags)
        return self.FMT_FILENAME % {
                'dir': self.basedir,
                'prefix': self.PREFIX,
                'sync_fn': generate_sync_fn_part(self.sync_id, idx),
                'tags': taglist,
                'flags': self.flags(tags),
                'suffix': self.SUFFIX}

    def transform(self, metadata, message):
        ts = metadata.timestamp
        if message is not None:
            message = self.Transform(metadata, message,
                add_from=self.MANGLE_ADD_FROM,
                add_headers=self.MANGLE_ADD_HEADERS,
                add_moggie_sync=(self.MANGLE_ADD_HEADERS and self.sync_id),
                mangle_from=False,
                fix_newlines=self.eol)
        return (self.get_filename(metadata), ts, message)

    def delete(self, metadata, filename=None):
        self.writer.delete_file(filename or self.get_filename(metadata))

    def export(self, metadata, message):
        filename, ts, message = self.transform(metadata, message)
        if self.writer.CAN_DELETE:
            prefix = '-'.join(filename.split('-')[:2])
            self.writer.delete_by_prefix(prefix)
        self.writer.add_file(filename, ts, message)

    def compact(self):
        self.writer.compact()

    def close(self):
        self.writer.close()


class EmlExporter(MaildirExporter):
    SUBDIRS = []
    PREFIX = ''
    SUFFIX = '.eml'
    MANGLE_ADD_FROM = True
    MANGLE_ADD_HEADERS = True
    MANGLE_FIX_EOL = b'\r\n'   # .EML is a Windows thing?

    AS_DEFAULT = MaildirExporter.AS_ZIP

    def flags(self, tags):
        return ''

    def default_basedir(self):
        return ''


if __name__ == '__main__':
    import sys, time
    from ...email.metadata import Metadata

    now = int(time.time())
    md = Metadata.ghost(msgid='<testing@moggie>')
    md[md.OFS_TIMESTAMP] = now
    md.more['tags'] = ['inbox', 'read']

    bio = ClosableBytesIO()
    with EmlExporter(bio, password=b'testing') as exp:
        for i in range(0, 4):
            exp.export(md, b"""\
From: bre@example.org
To: bre@example.org
Date: Thu, 1 Sep 2022 03:37:29 +0200 (CEST)
Message-ID: <testing@moggie>
Status: N
Subject: ohai

This is very nice.
From Iceland with Love!
>From Iceland with more Love!
Why does mutt not unescape?""")

    exported = bio.dump()
    sys.stdout.buffer.write(exported)

