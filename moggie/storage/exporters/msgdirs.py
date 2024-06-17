import base64
import json
import logging

from .maildir import MaildirExporter


class MsgdirsExporter(MaildirExporter):
    """
    Export parsed messages as a zipped or tarred directory structure.
    """
    SUBDIRS = []
    PREFIX = ''
    FMT_DIRNAME = 'messages.%x'
    FMT_FILENAME = '%(dir)smoggie%(sync_fn)smsg'

    # FIXME: Extract this, it's definitly dup code
    EXT_MAP = {
        'text/plain': 'txt',
        'text/html': 'html'}

    def part_filename(self, idx, part):
        # FIXME: Sanitize things here, this is potentially dangerous
        ct = part.get('content-type', ['text/plain', {}]) 
        cd = part.get('content-disposition', ['inline', {}])
        xt = self.EXT_MAP.get(ct[0], 'dat')
        fn = cd[1].get('filename') or ct[1].get('name') or ('part.' + xt)
        return '%2.2d.%s.%s' % (idx + 1, cd[0], fn)

    def export_parsed(self, metadata, parsed, friendly):
        dirname, ts, _ = self.transform(metadata, None)
        if self.writer.CAN_DELETE:
            prefix = '-'.join(dirname.split('-')[:2])
            self.writer.delete_by_prefix(prefix)
        dirname += '/'

        self.writer.add_file(dirname + 'message.txt', ts,
            bytes(friendly, 'utf-8'))

        for i, part in enumerate(parsed['email']['_PARTS']):
            if '_TEXT' in part:
                part['_FILE'] = fn = self.part_filename(i, part)
                self.writer.add_file(dirname + fn, ts, bytes(part['_TEXT'], 'utf-8'))

            elif '_RAW' in part:
                part['_FILE'] = fn = self.part_filename(i, part)
                self.writer.add_file(dirname + fn, ts, base64.b64decode(part['_RAW']))

            # Cleanup!
            for key in ('_TEXT', '_RAW', '_HTML_TEXT', '_HTML_CLEAN'):
                if key in part:
                    del part[key]

        self.writer.add_file(dirname + 'structure.json', ts,
            bytes(json.dumps(parsed['email'], indent=2), 'utf-8'))


if __name__ == '__main__':
    pass
