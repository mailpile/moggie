import binascii
import base64
import re

from .headers import parse_header
from .rfc2074 import quoted_printable_to_bytearray


class MessagePart(dict):
    """
    This is a lazy, incremental MIME parser.

    The output is a dictionary which is guaranteed to be JSON serializable;
    binary blobs (if any are requested) will be base-64 encoded.

    In the spirit of doing as little work as possible, it doesn't copy any
    of the source message content around until it is explicity asked to
    provide data (raw or decoded).
    """
    ESCAPED_FROM = re.compile(r'(^|\n)\>(\>*From)')

    def __init__(self, msg_bin, fix_mbox_from=False, inherit=None):
        self.inherit = inherit or {}
        self.msg_bin = [msg_bin]
        self.fix_mbox_from = fix_mbox_from
        self.hend = len(msg_bin)
        self.eol = b'\n'
        for eol, hend in (
                (b'\r\n', b'\r\n\r\n'),
                (b'\n',   b'\n\n'),
                (b'\r\n', b'\n\r\n')):  # This one is weird!
            try:
                self.hend = msg_bin.index(hend)
                self.eol = eol
                break
            except ValueError:
                pass

        self.update(parse_header(msg_bin[:self.hend]))
        self.update(self.inherit)

    def _find_parts_re(self, boundary, buf_idx=0):
        boundary = b'\n--' + bytes(boundary, 'latin-1')
        msg_bin = self.msg_bin[buf_idx]
        body_beg = self.hend + 2*len(self.eol)
        body_end = len(msg_bin)
        bounds = list(re.finditer(boundary + b'(--)?[ \t]*\r?\n?', msg_bin))
        begs = [body_beg] + [m.span()[1] for m in bounds]
        ends = [m.span()[0] for m in bounds] + [body_end]
        return begs, ends

    def _find_parts(self, boundary, buf_idx=0):
        """
        This does roughly the same thing as _find_parts_re, but avoids the
        regexp engine, because of the risk that boundary strings contain
        regexp syntax. It's also even faster for large messages.
        """
        buf = self.msg_bin[buf_idx]
        boundary = b'\n--' + bytes(boundary, 'latin-1')
        body_beg = self.hend + 2*len(self.eol)
        body_end = len(buf)

        last_b = 0
        bounds = []
        stop = False
        while not stop:
            # Find the beginning of our next boundary string
            b = buf.find(boundary)
            if b < 0:
                break

            # Find the end of the boundary string, including trailing
            # whitespace. Detect and respect the end marker (stop).
            e = b + len(boundary)
            if buf[e:e+2] == b'--':
                stop = True
                e += 2
            while (e < len(buf)) and buf[e] in b' \t':
                e += 1
            if buf[e:e+len(self.eol)] == self.eol:
                e += len(self.eol)

            bounds.append((b+last_b, e+last_b))
            # Rewind slightly, in case our input has incorrect whitespace.
            last_b += (e-2)
            buf = buf[(e-2):]

        begs = [body_beg] + [e for b,e in bounds]
        ends = [b for b,e in bounds] + [body_end]
        return begs, ends

    def with_structure(self, recurse=True, buf_idx=0):
        """
        Add _PARTS to the parse tree: a list of dicts, each of which
        represents a part of the message. The 0th element of the list is
        the message itself. Note that even though MIME messages have a
        nested (onion-like) structure, in _PARTS this structure is
        flattened to a single list, but attributes _DEPTH and _PARTS
        added to explain where this part is within the overall structure
        of the message.
        """
        if '_PARTS' in self:
            return self
        self['_PARTS'] = parts = []

        ct, ctp = (self.get('content-type') or ('text/plain', {}))
        cd, cdp = (self.get('content-disposition') or ('inline', {}))
        body_beg = self.hend + 2*len(self.eol)
        body_end = len(self.msg_bin[buf_idx])
        parts.append({
            'content-transfer-encoding': self.get('content-transfer-encoding', '8bit'),
            'content-disposition': [cd, cdp],
            'content-type': [ct, ctp],
            '_BUF': buf_idx,
            '_BYTES': [0, body_beg, body_end],
            '_DEPTH': 0})
        parts[-1].update(self.inherit)

        if (ct and ct.startswith('multipart/')) and ('boundary' in ctp):
            parts[0]['_PARTS'] = 0
            begs, ends = self._find_parts(ctp['boundary'])
            for i, (beg, end) in enumerate(zip(begs, ends)):
                if beg >= end:
                    continue
                parts[0]['_PARTS'] += 1
                part = {
                    '_BUF': buf_idx,
                    '_BYTES': [beg, beg, end],
                    '_DEPTH': 1,
                    'content-transfer-encoding': '8bit'}
                part.update(self.inherit)
                parts.append(part)
                if i == 0:
                    part['content-type'] = ['text/x-mime-preamble', {}]
                elif i == len(begs)-1:
                    # FIXME: If the message is truncated and doesn't have a
                    #        closing MIME marker, we'll accidentally assign
                    #        an actual part to the postamble. We need to
                    #        detect this more explicitly based on --*--.
                    #        Or make sure find_parts copes!
                    if ((end - beg) < 2) and not self._raw(part).strip():
                        # Short white-space only postamble should just be ignored.
                        parts[0]['_PARTS'] -= 1
                        parts.pop(-1)
                    else:
                        part['content-type'] = ['text/x-mime-postamble', {}]
                elif recurse:
                    sub = MessagePart(
                        self._raw(part), self.fix_mbox_from,
                        inherit=self.inherit
                        ).with_structure(recurse)
                    for p in sub['_PARTS']:
                        p['_BUF'] = part['_BUF']
                        p['_BYTES'][0] += part['_BYTES'][0]
                        p['_BYTES'][1] += part['_BYTES'][0]
                        p['_BYTES'][2] += part['_BYTES'][0]
                        p['_DEPTH'] += part['_DEPTH']
                    part.update(sub)
                    part.update(sub['_PARTS'][0])
                    del part['_PARTS']
                    parts.extend(sub['_PARTS'][1:])
                else:
                    part['content-type'] = ['message/x-mime-part', {}]

        elif ct in ('text/plain', '', None):
            # FIXME: Search text/plain parts for:
            #   - Quoted content
            #   - Forwarded messages
            #   - Inline PGP encrypted/signed blobs
            pass

        elif ct == 'message/rfc822':
            # FIXME:
            #   - Is this a delivery failure report?
            #   - Is recursively parsing this anything but a recipe for bugs?
            # FIXME: Obey self.fix_mbox_from ?
            pass

        elif self.get('content-id'):
            cid = self['content-id'].strip()
            if cid[:1] == '<':
                cid = cid[1:-1]
            if cid:
                parts[-1]['content-id'] = cid

        return self

    def _raw(self, part, header=False):
        return self.msg_bin[part['_BUF']][
            part['_BYTES'][0 if header else 1]:part['_BYTES'][2]]

    def _bytes(self, part):
        encoding = part['content-transfer-encoding'].lower()
        raw_data = self._raw(part)

        if encoding == 'base64':
            return base64.b64decode(raw_data)

        # Not base64, do we need to undo mbox From mangling?
        if self.fix_mbox_from:
            pass  # FIXME

        if encoding == 'quoted-printable':
            return bytes(
                quoted_printable_to_bytearray(str(raw_data, 'latin-1')))

        return raw_data

    def _base64(self, part):
        encoding = part['content-transfer-encoding'].lower()
        if encoding == 'base64':
            return self._raw(part).strip()
        return base64.b64encode(self._bytes(part))

    def _text(self, part):
        ct, ctp = part['content-type']
        charsets = [ctp.get('charset', 'latin-1'), 'utf-8', 'latin-1']
        for cs in charsets:
            try:
                text = str(self._bytes(part), cs)
                if self.fix_mbox_from:
                    text = self.ESCAPED_FROM.sub(r'\1\2', text)
                return text
            except (UnicodeDecodeError, LookupError, binascii.Error) as e:
                pass
        return None

    def part_raw(self, idx, recurse=True, header=True):
        """
        Return the raw source of a single part of the message (as bytes).
        """
        return self._raw(self.with_structure(recurse=recurse)['_PARTS'][idx],
            header=header)

    def part_body(self, idx, recurse=True):
        """
        Return the decoded body of a single part of the message (as bytes).
        """
        return self._bytes(self.with_structure(recurse=recurse)['_PARTS'][idx])

    def part_text(self, idx, recurse=True, mime_types=[]):
        """
        Return decoded text of the message (as unicode).
        """
        part = self.with_structure(recurse=recurse)['_PARTS'][idx]
        ct = part['content-type'][0]
        if ((not mime_types and ct.startswith('text/'))
                or ct in mime_types):
            return self._text(part)
        else:
            return None

    def with_text(self, multipart=False, recurse=True):
        """
        Add _TEXT elements to each text part, containing a copy of the part
        body, decoded and presented as UTF-8 text.
        """
        self.with_structure(recurse=recurse)
        for part in self['_PARTS']:
            ct, ctp = part['content-type']
            if ct in ('text/plain', 'text/html'):
                if '_TEXT' not in part:
                    part['_TEXT'] = self._text(part)
        return self

    def with_data(self, multipart=False, text=False, recurse=True, only=None):
        """
        Add _DATA elements to each part (except multipart/ and text parts,
        unless explicitly requested) containing a base64-encoded copy of the
        decoded part body.
        """
        self.with_structure(recurse=recurse)
        for i, part in enumerate(self['_PARTS']):
            if only and (i in only):
                part['_DATA'] = str(self._base64(part), 'latin-1')
            else:
                ct, ctp = part.get('content-type', ['', {}])
                if ('_DATA' not in part and
                        (multipart or not ct.startswith('multipart/')) and
                        (text or not ct in ('text/plain', 'text/html'))):
                    part['_DATA'] = str(self._base64(part), 'latin-1')
        return self

    def with_full_raw(self):
        """
        Add a _RAW elements for the complete, unparsed message.
        """
        self['_RAW'] = str(base64.b64encode(self.msg_bin[0]), 'latin-1')
        return self

    def with_raw(self, multipart=False, recurse=True):
        """
        Add _RAW elements to each part (except multipart/ and text parts,
        unless explicitly requested) containing a base64-encoded copy of the
        raw part source.
        """
        self.with_structure(recurse=recurse)
        for part in self['_PARTS']:
            ct, ctp = part.get('content-type', ['', {}])
            if multipart or not ct.startswith('multipart/'):
                part['_RAW'] = str(
                    base64.b64encode(self._raw(part, header=True)), 'latin-1')
        return self

    @classmethod
    def iter_parts(cls, ptree, full=False):
        idx = 0
        parts = ptree.get('_PARTS', [])
        if full:
            yield from iter(parts)
            return
        while idx < len(parts):
            while '_REPLACE' in parts[idx]:
                idx = parts[idx]['_REPLACE']
            yield parts[idx]
            while '_REPLACED' in parts[idx]:
                idx = parts[idx]['_REPLACED']
            if parts[idx].get('_LAST'):
                return
            idx += 1

    def decrypt(self, decryptors, skip=None, max_passes=2):
        """
        This will decrypt any encrypted parts, appending decrypted blobs
        to the self.msg_bin list and updating the _PARTS list to reference
        their contents. Decrypted parts are appended to the end of the
        _PARTS list, but _REPLACE and _REPLACED markers are added to suggest
        the logical order of the results after decrypting.

        Each pass of this function looks at each part exactly once; since
        people can nest signed and encrypted parts inside each other, it
        makes sense to allow multiple passes. The default is two.
        """
        self.with_structure()
        new_parts = []
        non_mime = 0   # Note: Resets to zero on each pass. This is sane-ish,
                       #       Because the second pass can only contain output
                       # from decryption during the first, so the entire Nth
                       # pass can be considered part of pass 1. But this is
                       # still kinda wrong. FIXME?
        changed = 0
        for i, part in enumerate(self['_PARTS']):
            if skip and (i <= skip):
                continue
            if '_REPLACE' in part:
                continue

            ct, ctp = part.get('content-type', ['', {}])
            cd, cdp = part.get('content-disposition', ['', {}])
            first = ':first' if (non_mime < 1) else ''

            candidates = decryptors.get(ct+first) or decryptors.get(ct, [])
            if not (ct.startswith('multipart/') or ct == 'text/x-mime-preamble'):
                non_mime += 1
                filename = ctp.get('name') or cdp.get('filename')
                if filename and '.' in filename:
                    ext = filename.rsplit('.', 1)[-1]
                    candidates.extend(
                        decryptors.get(ext+first) or decryptors.get(ext, []))

            errors = []
            decrypted = None
            for decryptor in candidates:
                try:
                    decrypted = decryptor(self._bytes(part), i, part, self)
                    if decrypted is not None:
                        replace, parts = decrypted
                        infos = []
                        for info, cleartext in parts:
                            self.msg_bin.append(cleartext)
                            info['_BUF'] = len(self.msg_bin) - 1
                            info['_BYTES'] = [0, 0, len(cleartext)]
                            info['_DEPTH'] = (
                                info.get('_DEPTH', 0) + part['_DEPTH'])
                            if 'content-transfer-encoding' not in info:
                                info['content-transfer-encoding'] = '8bit'
                            infos.append(info)
                            changed += 1
                        new_parts.append((replace, infos))
                        break
                except Exception as e:
                    errors.append(str(e))

            if errors:
                part['_DECRYPT_FAILED'] = errors

        if not skip:
            part['_LAST'] = True
        processed = i

        for b_e, parts in new_parts:
            if b_e and parts and parts[0]:
                b, e = b_e
                part = parts[0]
                if (part.get('content-type', [''])[0].startswith('multipart/')
                        and len(parts) == 1):
                    inherit = {}
                    if '_CRYPTO' in part:
                        inherit['_CRYPTO'] = part['_CRYPTO']
                    sub = MessagePart(self._raw(part), inherit=inherit)
                    sub.with_structure(True)
                    for p in sub['_PARTS']:
                        p['_BUF'] = part['_BUF']
                        p['_BYTES'][0] += part['_BYTES'][0]
                        p['_BYTES'][1] += part['_BYTES'][0]
                        p['_BYTES'][2] += part['_BYTES'][0]
                        p['_DEPTH'] += part['_DEPTH']
                    part.update(sub)
                    part.update(sub['_PARTS'][0])
                    del part['_PARTS']
                    parts.extend(sub['_PARTS'][1:])

                self['_PARTS'][b]['_REPLACE'] = len(self['_PARTS'])
                self['_PARTS'].extend(parts)
                self['_PARTS'][-1]['_REPLACED'] = e-1

                changed += 1

        self['_DECRYPTED'] = self.get('_DECRYPTED', 0) + changed
        if changed and (max_passes > 1):
            return self.decrypt(decryptors,
                skip=processed,
                max_passes=(max_passes - 1))
        else:
            return self

    MAGIC_MIMEINFO = {
        'README.txt': (1, 0, None, None),
        'message-body.txt': (5, 1, 'text/plain', 'inline'),
        'message-body.html': (5, 1, 'text/html', 'inline')}
    EXT_MIMETYPES = {
        'txt': 'text/plain',
        'html': 'text/html'}

    def with_archive_contents(self,
            moggie_archives=True,
            zip_archives=True,
            zip_passwords=[]):
        """
        This will examine the contents of recognize archive types and
        add their contents to the list of message parts.
        """

        def _decrypt_zip_archive(part_bin, p_idx, part, parent):
            from io import BytesIO
            if zip_passwords:
                from pyzipper import AESZipFile as zfc
            else:
                from zipfile import ZipFile as zfc

            parts = []
            mimetype = part.get('content-type', [''])[0]
            magic = (mimetype in ('x-mailpile/zip', 'x-moggie/zip'))

            with zfc(BytesIO(part_bin), mode='r') as zf:
                if magic:
                    names = zf.namelist()
                    if ('message-body.txt' in names
                            or 'message-body.html' in names):
                        parts.append(({
                                '_ZIP': 'extracted',
                                'content-type': ['multipart/alternative', {}]
                            }, b''))

                for i, zi in enumerate(zf.infolist()):
                    depth = 0
                    dispo = 'attachment'
                    ext = (zi.filename or '').rsplit('.', 1)[-1]
                    mimetype = (self.EXT_MIMETYPES.get(ext)
                        or 'application/octet-stream')

                    if magic:
                        i_n_m_d = self.MAGIC_MIMEINFO.get(zi.filename)
                        if i_n_m_d:
                            max_i, n, m, d = i_n_m_d
                            if i < max_i:
                                if not (m and d):
                                    continue
                                depth, mimetype, dispo = n, m, d

                    unhappy = False
                    for pw in zip_passwords + ['']:
                        if zip_passwords:
                            zf.setpassword(bytes(pw, 'utf-8'))
                        try:
                            what = 'decrypted' if pw else 'extracted'
                            with zf.open(zi.filename, 'r') as fd:
                                info = {
                                    '_ZIP': what,
                                    '_DEPTH': depth,
                                    'content-type': [
                                        mimetype, {'name': zi.filename}],
                                    'content-disposition': [dispo, {}]}
                                parts.append((info, fd.read()))
                                unhappy = False
                                break
                        except RuntimeError as e:
                            unhappy = str(e)
                if unhappy:
                    raise PermissionError(unhappy)

            return (p_idx, p_idx+1), parts

        decryptors = {}
        if zip_archives:
            decryptors['zip'] = [_decrypt_zip_archive]
        elif moggie_archives:
            decryptors['x-mailpile/zip'] = [_decrypt_zip_archive]
            decryptors['x-moggie/zip'] = [_decrypt_zip_archive]

        # FIXME: We could support more archive types, if we wanted.
        #        Not sure there is any justification though!

        if decryptors:
            return self.decrypt(decryptors, max_passes=1)


def parse_message(msg_bin, fix_mbox_from=False):
    return MessagePart(msg_bin, fix_mbox_from)


if __name__ == '__main__':
    import copy, json, sys
    from ..util.dumbcode import *
    msg = b"""\
From bre  blah blah blah
From: Bjarni <bre@example.org>
To: Wilfred <wilfred@example.org>
Nothing: Yet
Subject: This is my subject
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="helloworld"

This is a multipart e-mail, if you see this then your mail client is silly.

--helloworld
Content-Type: multipart/alternative; boundary="ohai"

--ohai
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: base64
X-Extra-Header-Data: Oh yes!

VGhlcmUgYXJlIG1hbnkgZS1tYWlscyBvdXQgdGhlcmUsIGJ1dCB0aGlzIG9uZSBpcyBtaW5lLgpJ
dCBpcyBub3QgdmVyeSBzaW1wbGUuCg==

--ohai
Content-Type: text/html; charset=wonky
Content-Transfer-Encoding: quoted-printable

<p>There are _many_ e-mails out=20there, but=20this one is mine. =
It is not very simple.<br>
>From here to the moon!</p>

--ohai
Content-Type: application/octet-stream
Content-Transfer-Encoding: base64

AAAA==

--ohai--
--helloworld
Content-Type: multipart/encrypted; boundary="encwhee"; protocol="fake"

--encwhee
Content-Type: application/fake-encrypted

Version: 1

--encwhee
Content-Type: application/octet-stream

EE:Content-Type: multipart/mixed; boundary=eeee
EE:
EE:--eeee
EE:Content-Type: text/plain; charset="utf-8"
EE:
EE:These are the secret words. It's encrypted, I promise!
EE:
EE:--eeee--
--encwhee--
--helloworld--

Trailing garbage!
"""
    def decrypt_substitution(part_bin, p_idx, part, parent):
        import copy
        new_data = part_bin.replace(b' are ', b' were ')
        if new_data == part_bin:
            return None
        new_part = {
            'content-decrypted': ['substitution'] + part['content-type'],
            'content-type': ['text/plain', {'charset': 'utf-8'}]}
        return (p_idx, p_idx+1), [
#           (copy.copy(new_part), copy.copy(new_data)),
            (new_part, new_data)]

    def decrypt_multipart_encrypted(part_bin, p_idx, part, parent):
        if part['content-type'][1].get('protocol') != 'fake':
            print('Not fake, boo')
            return None

        mpart = parent['_PARTS'][p_idx + 1]
        ppart = parent['_PARTS'][p_idx + 2]
        mpart_content = str(parent._bytes(mpart), 'latin-1').strip()
        if (mpart_content.lower().split() != ['version:', '1']
                or ppart['content-type'][0] != 'application/octet-stream'):
            return None

        data = parent._bytes(ppart).replace(b'EE:', b'')
        new_part = {
            'content-decrypted': ['multipart-enc'] + part['content-type'],
            'content-type': ['multipart/mixed', {}]}

        return (p_idx, p_idx+3), [(new_part, data)]

    p = parse_message(msg, fix_mbox_from=True).with_structure()
    assert(list(p.iter_parts(p)) == list(p.iter_parts(p, full=True)))
    assert(len(list(p.iter_parts(p))) == 10)

    p.decrypt({
        'multipart/encrypted': [decrypt_multipart_encrypted],
        'text/plain:first': [decrypt_substitution]})
#   for i, part in enumerate(p['_PARTS']):
#       p['_PARTS'][i]['_'] = i
#   print(json.dumps(list(p.iter_parts(p)), indent=1))
    assert(len(list(p.iter_parts(p))) == 9)  # Version: 1 disappears

    assert(p.get('_DECRYPTED') == 4)
    assert(p.part_text(0) is None)
    assert(p.part_text(0, mime_types=('multipart/mixed',)) is not None)
    assert(p.part_text(1) is not None)  # Preamble is text
    assert(p.part_text(2) is None)
    assert(p.part_text(3).startswith('There are many e-mails out there,'))
    assert(p.part_text(4).startswith('<p>There are _many_ e-mails out there'))
    assert('\nFrom here to the moon' in p.part_text(4))  # fix_mbox_from
    assert(p.part_text(6) is None)
    assert(p.part_text(7) is None)
    assert(p.part_text(8) is None)

    assert('_TEXT' not in p['_PARTS'][3])
    assert('_DATA' not in p['_PARTS'][3])
    p.with_text().with_data()

    assert('_DATA' not in p['_PARTS'][3])
    assert(p['_PARTS'][3]['_TEXT'].startswith('There are many'))
    assert(p['_PARTS'][10]['_TEXT'].startswith('There were many'))
    assert(p['_PARTS'][4]['_TEXT'].startswith('<p>There are _many_'))
    assert(p['_PARTS'][12]['_TEXT'].startswith('These are the secret'))
    # We asserted :first on the are->were decryptor, so no 13th part.
    #assert(p['_PARTS'][13]['_TEXT'].startswith('These were the secret'))
    assert(len(p['_PARTS']) == 13)

    assert('_TEXT' not in p['_PARTS'][9])
    assert('_DATA' in p['_PARTS'][9])
    assert('Trailing garbage!' in p.part_text(9))
    print('Tests OK')

    from email.parser import BytesParser, BytesFeedParser
    import time

    #print('%s' % json.dumps(p, indent=2))
    #sys.exit(0)

    for size in (1, 100, 1000, 10000):
        msg2 = msg.replace(b'AAAA==',
            b'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n' * size)

        t0 = time.time()
        for i in range(0, 1000):
            BytesParser().parsebytes(msg2)
        t1 = time.time()
        for i in range(0, 1000):
            parse_message(msg2).with_text().with_data()
        t2 = time.time()

        print('Perf: %.2fs/1k %d-byte e-mail (vs. %.2fs/1k)'
            % (t2-t1, len(msg2), t1-t0))

