import base64
import datetime
import json
import logging
import os
import time

import markdown

from ...security.filenames import clean_filename
from ...util.friendly import friendly_datetime
from ...email.parsemime import parse_message
from .maildir import MaildirExporter


_b = lambda t: bytes(t, 'utf-8') if isinstance(t, str) else t
_t = lambda b: b if isinstance(b, str) else str(b, 'utf-8')


def ImportDirectory(basedir, filelist, opener=open, getmtime=os.path.getmtime):
    """
    This reverses the export below, loading the contents of a directory
    into a message parse structure. If there is no structure.json, a sane
    default will be assumed.

    If the filelist is a string or bytes object, it will be treated as a
    path and os.listdir() used to read the contents.

    Usage:

        parsed = ImportDirectory(path)
        parsed = ImportDirectory(os.listdir(path))
    """
    if isinstance(filelist, (str, bytes)):
        basedir = os.path.join(basedir, filelist)
        filelist = os.listdir(basedir)

    # Cleanup file list and basedir before we start using them
    basedir = _b(basedir)
    filelist = [fn for fn in [_b(f) for f in filelist]
        if fn not in (b'.', b'..', b'structure.json', b'message.txt')]

    try:
        structure_path = os.path.join(basedir, b'structure.json')
        with opener(structure_path) as fd:
            structure = json.loads(fd.read())
            generated = getmtime(structure_path)
    except (IOError, OSError):
        structure = {'_PARTS': []}
        generated = 0

    # Iterate through _PARTS and load any file data, removing from filelist as we go
    parts = structure['_PARTS']
    removing = []
    for i, part in enumerate(parts):
        if '_FILE' in part:
            fn, mode, target, _e = _b(part['_FILE']), 'rb', '_DATA', base64.b64encode
            del part['_FILE']
        elif '_TEXTFILE' in part:
            fn, mode, target, _e = _b(part['_TEXTFILE']), 'r', '_TEXT', lambda t: t
            del part['_TEXTFILE']
        else:
            continue

        try:
            filelist.remove(fn)
            datafile = os.path.join(basedir, fn)
            with opener(datafile, mode) as fd:
                part[target] = _t(_e(fd.read()).strip())
                part['_UPDATED'] = getmtime(datafile)
        except (IOError, OSError, ValueError):
            removing.append(i)

    # Removing parts if the corresponding file is gone
    for i in reversed(removing):
        parts.pop(i)

    # Iterate through filelist, add any new attachments
    for fn in filelist:
        part = {
            'content-type': ['application/octet-stream', {}],
            'content-disposition': ['attachment', {'filename': _t(fn)}]}
        with opener(os.path.join(basedir, fn), 'rb') as fd:
            part['_DATA'] = _t(base64.b64encode(fd.read()).strip())
        parts.append(part)

    # Check if 'message.txt' exists and is newer than generated - if so, use
    # it to update message headers and content
    try:
        message_txt = os.path.join(basedir, b'message.txt')
        update_ts = getmtime(message_txt)

        if update_ts > generated:
            with opener(message_txt, 'rb') as fd:
                updates = parse_message(fd.read()).with_text()

            # Update/overwrite headers
            for k in updates:
                if k[:1] != '_':
                    structure[k] = updates[k]

            # Update message text/html
            new_text = updates['_PARTS'][0]['_TEXT']
            new_html = markdown.markdown(new_text)
            updated = 0
            for content, ctype in (
                    (new_text, 'text/plain'),
                    (new_html, 'text/html')):
                for part in parts:
                    if ((part['content-type'][0] == ctype) and
                            (part['content-disposition'][0] == 'inline')):
                        if part.get('_UPDATED', 0) < update_ts:
                            part['_TEXT'] = content
                            updated += 1
                        break

            if not updated:
                parts[:0] = [{
                    'content-type': ['text/plain', {'charset': 'utf-8'}],
                    'content-disposition': ['inline', {}],
                    '_TEXT': new_text}]

    except (IOError, OSError):
        pass

    return structure


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

        pmd = metadata.parsed()
        dirname = '%s__%s__%s' % (
            hhmm.replace(':', ''),
            pmd['from'].address if pmd.get('from') else '(unknown)',
            pmd.get('subject') or '(no subject)')

        dirname = os.path.join(self.basedir, yyyymmdd, clean_filename(dirname))
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
                part['_TEXTFILE'] = fn = self.part_filename(count, part)
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
    import sys
    filepath = os.path.abspath(sys.argv[1])
    base, dn = os.path.split(filepath)
    print(json.dumps(ImportDirectory(base, dn), indent=2))
