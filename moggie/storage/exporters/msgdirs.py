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
        'text/x-mime-preamble': 'txt',
        'text/x-mime-postamble': 'txt',
        'text/plain': 'txt',
        'text/html': 'html'}

    def __init__(self, *args, **kwargs):
        if not kwargs.get('dirname'):
            kwargs['dirname'] = 'E-Mail'
        super().__init__(*args, **kwargs)

    def part_filename(self, idx, part):
        ct = part.get('content-type', ['text/plain', {}]) 
        dd = 'hidden' if ct[0].startswith('text/x-mime-') else 'inline'
        cd = part.get('content-disposition', [dd, {}])
        xt = '.' + self.EXT_MAP.get(ct[0], 'dat')
        fn = cd[1].get('filename') or ct[1].get('name') or (cd[0] + xt)
        return '%2.2d.%s' % (idx, clean_filename(fn))

    def get_dirname_and_ts(self, metadata):
        yyyymmdd, hhmm = friendly_datetime(metadata.timestamp).split()

        pmeta = metadata.parsed()
        dirname = '%s__%s' % (
            hhmm.replace(':', ''),
            pmeta.get('subject') or '(no subject)')

        dirname = os.path.join(self.basedir, yyyymmdd,
            clean_filename(pmeta['from'].address),
            clean_filename(dirname))

        return dirname, metadata.timestamp

    def export_parsed(self, metadata, parsed, friendly):
        dirname, ts = self.get_dirname_and_ts(metadata)
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
