import base64
import datetime
import json
import logging
import os
import time

from ...security.filenames import clean_filename
from ...util.friendly import friendly_datetime
from .maildir import MaildirExporter


class MsgdirsExporter(MaildirExporter):
    """
    Export parsed messages as a zipped or tarred directory structure.
    """
    SUBDIRS = []
    PREFIX = ''
    FMT_FILENAME = '%(dir)smoggie%(sync_fn)smsg'

    # FIXME: Extract this, it's definitly dup code
    EXT_MAP = {
        'image/jpeg': 'jpg',
        'image/gif': 'gif',
        'application/pdf': 'pdf',
        'text/plain': 'txt',
        'text/html': 'html'}

    def __init__(self, *args, **kwargs):
        if not kwargs.get('dirname'):
            now = friendly_datetime(time.time())
            now = now.replace('-', '').replace(' ', '-').replace(':', '')
            kwargs['dirname'] = 'messages.%s' % now
        self.counter = 0
        super().__init__(*args, **kwargs)

    def part_filename(self, idx, part):
        ct = part.get('content-type', ['text/plain', {}]) 
        cd = part.get('content-disposition', ['inline', {}])
        xt = '.' + self.EXT_MAP.get(ct[0], 'dat')
        fn = cd[1].get('filename') or ct[1].get('name') or (cd[0] + xt)
        return '%2.2d.%s' % (idx, clean_filename(fn))

    def transform(self, metadata, message):
        dirname, ts, _ = super().transform(metadata, message)
        pmeta = metadata.parsed()
        self.counter += 1
        from_subj = '%3.3d - %s - %s' % (
            self.counter,
            pmeta['from'].address,
            pmeta.get('subject') or '(no subject)')
        dirname = os.path.join(
            os.path.dirname(dirname), clean_filename(from_subj))
        return dirname, ts, None

    def export_parsed(self, metadata, parsed, friendly):
        dirname, ts, _ = self.transform(metadata, None)
        if self.writer.CAN_DELETE:
            prefix = '-'.join(dirname.split('-')[:2])
            self.writer.delete_by_prefix(prefix)
        dirname += '/'

        self.writer.add_file(dirname + 'message.txt', ts,
            bytes(friendly, 'utf-8'))

        count = 1
        for part in parsed['email']['_PARTS']:
            if '_TEXT' in part:
                part['_FILE'] = fn = self.part_filename(count, part)
                self.writer.add_file(dirname + fn, ts, bytes(part['_TEXT'], 'utf-8'))
                count += 1

            elif '_DATA' in part:
                part['_FILE'] = fn = self.part_filename(count, part)
                self.writer.add_file(dirname + fn, ts, base64.b64decode(part['_DATA']))
                count += 1

            # Cleanup!
            for key in ('_TEXT', '_DATA', '_RAW', '_HTML_TEXT', '_HTML_CLEAN'):
                if key in part:
                    del part[key]

        self.writer.add_file(dirname + 'structure.json', ts,
            bytes(json.dumps(parsed['email'], indent=1), 'utf-8'))


if __name__ == '__main__':
    pass
