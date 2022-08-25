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
    def __init__(self, msg_bin, fix_mbox_from=False):
        self.msg_bin = msg_bin
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

    def _find_parts_re(self, boundary):
        boundary = self.eol + b'--' + bytes(boundary, 'latin-1')
        body_beg = self.hend + 2*len(self.eol)
        body_end = len(self.msg_bin)
        bounds = list(re.finditer(boundary + b'(--)?[ \t]*\r?\n?', self.msg_bin))
        begs = [body_beg] + [m.span()[1] for m in bounds]
        ends = [m.span()[0] for m in bounds] + [body_end]
        return begs, ends

    def _find_parts(self, boundary):
        """
        This does roughly the same thing as _find_parts_re, but avoids the
        regexp engine, because of the risk that boundary strings contain
        regexp syntax. It's also even faster for large messages.
        """
        boundary = self.eol + b'--' + bytes(boundary, 'latin-1')
        body_beg = self.hend + 2*len(self.eol)
        body_end = len(self.msg_bin)

        buf = self.msg_bin
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

    def with_structure(self, recurse=True):
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
        body_beg = self.hend + 2*len(self.eol)
        body_end = len(self.msg_bin)
        parts.append({
            'content-transfer-encoding': self.get('content-transfer-encoding', '8bit'),
            'content-type': [ct, ctp],
            '_BYTES': [0, body_beg, body_end],
            '_DEPTH': 0})

        if (ct and ct.startswith('multipart/')) and ('boundary' in ctp):
            parts[0]['_PARTS'] = 0
            begs, ends = self._find_parts(ctp['boundary'])
            for i, (beg, end) in enumerate(zip(begs, ends)):
                if beg >= end:
                    continue
                parts[0]['_PARTS'] += 1
                part = {
                    '_BYTES': [beg, beg, end],
                    '_DEPTH': 1,
                    'content-transfer-encoding': '8bit'}
                parts.append(part)
                if i == 0:
                    part['content-type'] = ['text/x-mime-preamble', {}]
                elif i == len(begs)-1:
                    part['content-type'] = ['text/x-mime-postamble', {}]
                elif recurse:
                    sub = MessagePart(
                        self._raw(part), self.fix_mbox_from
                        ).with_structure(recurse)
                    for p in sub['_PARTS']:
                        p['_BYTES'][0] += part['_BYTES'][0]
                        p['_BYTES'][1] += part['_BYTES'][0]
                        p['_BYTES'][2] += part['_BYTES'][0]
                        p['_DEPTH'] += 1
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
            pass

        return self

    def _raw(self, part, header=False):
        return self.msg_bin[
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
                return str(self._bytes(part), cs)
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
                part['_TEXT'] = self._text(part)
        return self

    def with_data(self, multipart=False, text=False, recurse=True):
        """
        Add _DATA elements to each part (except multipart/ and text parts,
        unless explicitly requested) containing a base64-encoded copy of the
        decoded part body.
        """
        self.with_structure(recurse=recurse)
        for part in self['_PARTS']:
            ct, ctp = part.get('content-type', ['', {}])
            if ((multipart or not ct.startswith('multipart/')) and
                    (text or not ct in ('text/plain', 'text/html'))):
                part['_DATA'] = str(self._base64(part), 'latin-1')
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

    def decrypt(self, decryptors):
        # FIXME: Decrypt the content?
        #
        #   .. this can work pretty seamlessly by extending our msg_bin
        #      object with the unencrypted content, and generating parts
        #      with byte-ranges that now go beyond the end of the original
        #      messages.
        #
        self.with_structure()
        new_parts = []
        for i, part in enumerate(self['_PARTS']):
            ct, ctp = part.get('content-type', ['', {}])
            for decryptor in decryptors.get(ct, []):
                cleartext = decryptor(self._bytes(part), part, self)
                if cleartext:
                    # - Append the cleartext to our binary buffer
                    # - Create a new MessagePart with the contents
                    # break: we don't decrypt the same part twice.
                    break
        for i, part in new_parts:
            pass  # FIXME: Insert new parts into main stream.

        return self

    def check_signatures(self):
        self.with_structure()
        for part in self['_PARTS']:
            ct, ctp = part.get('content-type', ['', {}])
            if ct == 'multipart/signed':
                pass
        return self


def parse_message(msg_bin, fix_mbox_from=False):
    return MessagePart(msg_bin, fix_mbox_from)


if __name__ == '__main__':
    import json
    msg = b"""\
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
It is not very simple.</p>

--ohai
Content-Type: application/octet-stream
Content-Transfer-Encoding: base64

AAAA==

--ohai--
--helloworld--

Trailing garbage!
"""
    p = parse_message(msg).with_structure()

    assert(p.part_text(0) is None)
    assert(p.part_text(0, mime_types=('multipart/mixed',)) is not None)
    assert(p.part_text(1) is not None)  # Preamble is text
    assert(p.part_text(2) is None)
    assert(p.part_text(3).startswith('There are many e-mails out there,'))
    assert(p.part_text(4).startswith('<p>There are _many_ e-mails out there'))
    assert(p.part_text(5) is None)
    assert(p.part_text(6) is not None)

    assert('_TEXT' not in p['_PARTS'][3])
    assert('_DATA' not in p['_PARTS'][3])
    p.with_text().with_data()

    assert('_DATA' not in p['_PARTS'][3])
    assert(p['_PARTS'][3]['_TEXT'].startswith('There are many'))
    assert(p['_PARTS'][4]['_TEXT'].startswith('<p>There are _many_'))

    assert('_TEXT' not in p['_PARTS'][6])
    assert('_DATA' in p['_PARTS'][6])
    assert('Trailing garbage!' in p.part_text(6))
    print('Tests OK')

    from email.parser import BytesParser, BytesFeedParser
    import time

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

    #print('%s' % json.dumps(p, indent=2))
