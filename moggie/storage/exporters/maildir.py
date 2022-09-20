import copy
import datetime
import os
import time
import zipfile

from .base import *
from .mbox import MboxExporter

SEQ = 0


class ZipWriter:
    def __init__(self, fd, d_acl=0o040750, f_acl=0o000640):
        self.zf = zipfile.ZipFile(fd, mode='w')
        self.d_acl = d_acl
        self.f_acl = f_acl

    def _tt(self, ts):
        return datetime.datetime.fromtimestamp(ts).timetuple()

    def mkdir(self, dn, ts):
        dn = dn if (dn[-1:] == '/') else (dn + '/')
        dirent = zipfile.ZipInfo(filename=dn, date_time=self._tt(ts))
        dirent.compress_type = zipfile.ZIP_STORED
        dirent.external_attr = self.d_acl << 16  # Unix permissions
        dirent.external_attr |= 0x10             # MS-DOS directory flag
        self.zf.writestr(dirent, b'')

    def add_file(self, fn, ts, data):
        fi = zipfile.ZipInfo(filename=fn, date_time=self._tt(ts))
        fi.external_attr = self.f_acl << 16
        fi.compress_type = zipfile.ZIP_DEFLATED
        self.zf.writestr(fi, data)

    def close(self):
        self.zf.close()


class TarWriter(ZipWriter):
    pass


class MaildirExporter(BaseExporter):
    """
    Export messages as a zipped or tarred Maildir, generating filenames
    which include our read/unread status and match our internal
    metadata/tags.
    """
    AS_ZIP = 0
    AS_TAR = 1
    SUBDIRS = ('cur', 'new', 'tmp')
    PREFIX = 'cur/'
    SUFFIX = ''
    BASIC_FLAGS = {
        'forwarded': 'P',
        'bounced':   'P',
        'resent':    'P',
        'replied':   'R',
        'seen':      'S',
        'drafts':    'D',
        'flagged':   'F',
        'trash':     'T'}

    def __init__(self, real_fd, output=AS_ZIP):
        if output == self.AS_TAR:
            ocls = TarWriter
            self.sep = ':'
        else:
            ocls = ZipWriter
            self.sep = ';'

        now = int(time.time())
        self.real_fd = real_fd
        self.writer = ocls(real_fd)
        for sub in self.SUBDIRS:
            self.writer.mkdir(sub, now)

        super().__init__(self.writer)

    def flags(self, tags):
        flags = set()
        if 'unread' not in tags:
            flags.add(self.BASIC_FLAGS['seen'])
        for tag in tags:
            if tag in self.BASIC_FLAGS:
                flags.add(self.BASIC_FLAGS[tag])
        return '%s2,%s' % (self.sep, ''.join(sorted([f for f in flags])))

    def transform(self, metadata, message):
        global SEQ
        SEQ += 1

        ts = metadata.timestamp
        tags = copy.copy(metadata.more.get('tags', []))
        taglist = ','.join(tags)

        filename = ('%s%d.%d.moggie=%s%s%s'
            % (self.PREFIX, ts, SEQ, taglist, self.flags(tags), self.SUFFIX))

        return (filename, ts, message)

    def export(self, metadata, message):
        self.writer.add_file(*self.transform(metadata, message))


class EmlExporter(MaildirExporter):
    SUBDIRS = []
    PREFIX = ''
    SUFFIX = '.eml'

    def flags(self, tags):
        return ''

    def transform(self, metadata, message):
        message = MboxExporter.MboxTransform(metadata, message,
            mangle_from=False)
        return super().transform(metadata, message)


if __name__ == '__main__':
    import sys, time
    from ...email.metadata import Metadata

    now = int(time.time())
    md = Metadata.ghost(msgid='<testing@moggie>')
    md[md.OFS_TIMESTAMP] = now
    md.more['tags'] = ['inbox', 'unread']

    bio = ClosableBytesIO()
    with EmlExporter(bio) as exp:
        for i in range(0, 4):
            exp.export(md, b"""\
From: bre@example.org
To: bre@example.org
Date: Thu, 1 Sep 2022 03:37:29 +0200 (CEST)
Message-Id: <testing@moggie>
Status: N
Subject: ohai

This is very nice.
From Iceland with Love!
>From Iceland with more Love!
Why does mutt not unescape?""")

    exported = bio.dump()
    sys.stdout.buffer.write(exported)

