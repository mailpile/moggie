import datetime
import logging
import re
import time

from io import BytesIO

from moggie.email.sync import generate_sync_id, generate_sync_header
from ..formats.base import FormatBytes


class ClosableBytesIO(BytesIO):
    """
    Work around the fact that BytesIO becomes unusable on close(), but
    we want to work with interfaces like zipfile that close their files
    when they finish.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._closed = False

    def cleanup(self):
        super().close()

    def close(self):
        self._closed = True

    def dump(self):
        data = self.getvalue()
        self.cleanup()
        return data


class BaseExporter:
    STATUS_HEADER = b'Status:'
    TAGS_HEADER = b'Tags:'
    CL_HEADER = b'Content-Length:'

    def __init__(self, outfile,
            password=None, moggie_id=None, src=None, dest=None):
        self.fd = self._open_or_create(outfile)
        self.password = password
        self.moggie_id = moggie_id
        self.dest = dest
        if (dest or src) and moggie_id:
            self.sync_id = generate_sync_id(moggie_id, src, dest)
            logging.debug('%s: sync_id=%s (src=%s, dest=%s)'
                % (self, self.sync_id, src, dest))
        else:
            logging.debug('No sync ID, how sad!')
            self.sync_id = None

    def _open_or_create(self, fd):
        self.written = None
        mode = 'w'
        if isinstance(fd, (str, bytes)):
            try:
                fd = open(fd, 'r+b')
                mode = 'a'
            except OSError:
                fd = open(fd, 'w+b')
        try:
            fd.seek(0, 2)
            fd.tell()
        except:
            self.written = 0
        return fd
 
    def can_encrypt(self):
        return False

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        self.close()

    def close(self):
        self.fd.close()

    def compact(self):
        pass

    def delete(self, metadata, filename=None):
        raise IOError('Deletion is unavailable')

    def calculate_idx(self, beg, transformed):
        return int(FormatBytes.RangeToKey(beg, beg+len(transformed))[1:], 16)

    def export(self, metadata, message):
        beg = self.written if (self.written is not None) else self.fd.tell()
        data = self.transform(metadata, message)
        self.fd.write(data)
        if self.written is not None:
            self.written += len(data)
        return self.calculate_idx(beg, data)

    def transform(self, metadata, message):
        """Prepare the message for writing out to the archive."""
        return self.Transform(metadata, message, add_moggie_sync=self.sync_id)

    @classmethod
    def MakeMboxFrom(cls, timestamp=None, address=b'moggie@localhost'):
        timestamp = timestamp or ((time.time() // 300) * 300)
        dt = datetime.datetime.fromtimestamp(timestamp)
        dt = bytes(dt.strftime('%a %b %d %T %Y'), 'utf-8')
        return b'From %s  %s' % (address, dt)

    @classmethod
    def Transform(cls, metadata, message,
            add_from=False,
            add_headers=False,
            add_moggie_sync=None,  # Defaults to same value as add_headers
            mangle_from=False,
            fix_newlines=False):
        # Convert to bytes and escape any bare From or >From lines
        if mangle_from:
            message = re.sub(b'\n(>*)From ', b'\n>\\1From ', message)

        if fix_newlines:
            # mbox is a Unix format, so this is the newline convention we want.
            eol = b'\n' if (fix_newlines is True) else fix_newlines
            eol = bytes(eol, 'utf-8') if isinstance(eol, str) else eol
            if eol == b'\r\n':
                message = bytearray(
                    message.replace(b'\r', b'').replace(b'\n', b'\r\n'))
            else:
                message = bytearray(message.replace(b'\r\n', eol))
        else:
            # What is our newline convention?
            eol = b'\r\n' if (b'\r\n' in message[:256]) else b'\n'
            message = bytearray(message)

        # Add the leading From delimeter, if not already present
        if not message.startswith(b'From ') and add_from:
            message[:0] = bytearray(b'%s%s' % (
                cls.MakeMboxFrom(metadata.timestamp), eol))

        # Make sure we end with some newlines
        while not message.endswith(eol * 2):
            message += eol

        hend = message.find(eol * 2)
        def _add_or_update(header, new_value):
            h_beg = message[:hend].find(eol + header)
            if h_beg > 0:
                h_end = h_beg + 2 + message[h_beg+2:].find(eol)
            else:
                h_beg = h_end = hend
            message[h_beg:h_end] = eol + header + b' ' + new_value

        # Update message headers with tags
        if add_headers and (hend > 0):
            cl = len(message) - (hend + 3*len(eol))
            _add_or_update(cls.CL_HEADER, b'%d' % cl)

            if metadata:
                tags = [t.split(':')[-1] for t in metadata.more.get('tags', [])]
                if tags:
                    taglist = bytes(', '.join(tags), 'utf-8')
                    _add_or_update(cls.TAGS_HEADER, taglist)
                if 'read' in tags:
                    _add_or_update(cls.STATUS_HEADER, b'RO')
                else:
                    _add_or_update(cls.STATUS_HEADER, b'O')

                if add_moggie_sync:
                    h, v = generate_sync_header(add_moggie_sync, metadata.idx)
                    _add_or_update(h + b':', v)

        return message


if __name__ == '__main__':
    bio = ClosableBytesIO()

    with BaseExporter(bio) as exp:
        exp.export(None, b"""\
From: bre@example.org
To: bre@example.org
Subject: ohai

Hello world!
""")

    assert(bio.dump().startswith(b'From: bre'))

    print('Tests passed OK')
