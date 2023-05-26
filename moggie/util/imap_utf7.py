# -*- coding: utf-8- -*-

# Originally from:
#     http://piao-tech.blogspot.no/2010/03/get-offlineimap-working-with-non-ascii.html#resources
#
# Python 3 port by Julien:
#     https://github.com/MarechJ/py3_imap_utf7/blob/master/imap_utf7.py

import binascii
import codecs

# encoding

def modified_base64 (s):
    s = s.encode('utf-16be')
    return  binascii.b2a_base64(s).rstrip(b'\n=').replace(b'/', b',')

def doB64(_in, r):
    if _in:
        r.append(b'&' + modified_base64(''.join(_in)) + b'-')
    del _in[:]

def encoder(s:str):
    r = []
    _in = []
    for c in s:
        ordC = ord(c)
        if 0x20 <= ordC <= 0x25 or 0x27 <= ordC <= 0x7e:
            doB64(_in, r)
            r.append(c.encode())
        elif c == '&':
            doB64(_in, r)
            r.append(b'&-')
        else:
            _in.append(c)
    doB64(_in, r)
    return (b''.join(r), len(r))

# decoding
def modified_unbase64(s):
    b = binascii.a2b_base64(s.replace(b',', b'/') + b'===')
    return b.decode('utf-16be')

def decoder(s:bytes):
    r = []
    decode = bytearray()
    for c in s:
        if c == ord('&') and not decode:
            decode.append(ord('&'))
        elif c == ord('-') and decode:
            if len(decode) == 1:
                r.append('&')
            else:
                ab = modified_unbase64(decode[1:])
                r.append(ab)
            decode = bytearray()
        elif decode:
            decode.append(c)
        else:
            r.append(chr(c))

    if decode:
        r.append(modified_unbase64(decode[1:]))

    bin_str = ''.join(r)
    return (bin_str, len(r))

class StreamReader (codecs.StreamReader):
    def decode (self, s, errors='strict'):
        return decoder(s)

class StreamWriter (codecs.StreamWriter):
    def decode (self, s, errors='strict'):
        return encoder(s)

def imap4_utf_7(name):
    if name in ('imap4-utf-7', 'imap4_utf_7'):
        return (encoder, decoder, StreamReader, StreamWriter)

codecs.register(imap4_utf_7)
