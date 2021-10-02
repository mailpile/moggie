import binascii
import json
import zlib

from urllib.parse import quote, unquote, unquote_to_bytes

from ..crypto.aes_utils import aes_ctr_encrypt, aes_ctr_decrypt, make_aes_key


DUMB_DECODERS = {}


def dumb_encode_bin(v, compress=False, aes_key_iv=None):
    if aes_key_iv:
        key, iv = aes_key_iv
        assert(len(iv) == 16)
        encoded = dumb_encode_bin(v, compress=compress)
        encrypted = aes_ctr_encrypt(key, iv, encoded)
        return b'e' + iv + encrypted

    if compress:
        encoded = dumb_encode_bin(v, compress=False)
        if len(encoded) > compress:
            compressed = zlib.compress(encoded)
            if len(compressed) < len(encoded):
                return b'z' + compressed
        return encoded

    if isinstance(v, bytes):     return (b'b' + v)
    if isinstance(v, str):       return (b'u' + v.encode('utf-8'))
    if isinstance(v, bool):      return (b'y' if v else b'n')
    if isinstance(v, int):       return (b'd%d' % v)
    if isinstance(v, float):     return (b'f%f' % v)
    if isinstance(v, bytearray): return (b'b' + bytes(v))
    if v is None:                return (b'-')

    if hasattr(v, 'dumb_encode_bin'): return v.dumb_encode_bin()

    if isinstance(v, (dict, list, set, tuple)):
        if isinstance(v, set):
            pfx, v = b's', list(v)
        elif isinstance(v, tuple):
            pfx, v = b't', list(v)
        else:
            pfx = b'j'
        j = json.dumps(v, separators=(',',':'), ensure_ascii=False)
        return (pfx + j.encode('utf-8'))

    raise ValueError('Unsupported type: <%s> = %s' % (type(v), v))


def dumb_encode_asc(v, compress=False, aes_key_iv=None):
    if aes_key_iv:
        key, iv = aes_key_iv
        assert(len(iv) == 16)
        encoded = dumb_encode_bin(v, compress=compress)
        encrypted = aes_ctr_encrypt(key, iv, encoded)
        return 'E' + str(binascii.b2a_base64(iv + encrypted, newline=False), 'latin-1')

    try:
        if compress and len(v) > compress:
            compressed = str(binascii.b2a_base64(
                zlib.compress(dumb_encode_bin(v, compress=False)),
                newline=False), 'latin-1')
            if len(compressed) < len(v):
                return 'Z' + compressed
    except TypeError:
        pass

    if isinstance(v, bytes):     return ('B' + str(binascii.b2a_base64(v, newline=False), 'latin-1'))
    if isinstance(v, str):       return ('U' + quote(v))
    if isinstance(v, bool):      return ('y' if v else 'n')
    if isinstance(v, int):       return ('d%d' % v)
    if isinstance(v, float):     return ('f%f' % v)
    if isinstance(v, bytearray): return ('B' + str(binascii.b2a_base64(v, newline=False), 'latin-1'))
    if v is None:                return ('-')

    if hasattr(v, 'dumb_encode_asc'): return v.dumb_encode_asc()

    if isinstance(v, (list, dict, set, tuple)):
        if isinstance(v, set):
            pfx, v = 'S', list(v)
        elif isinstance(v, tuple):
            pfx, v = 'T', list(v)
        else:
            pfx = 'J'
        j = json.dumps(v, separators=(',',':'), ensure_ascii=False)
        return (pfx + quote(j))

    raise ValueError('Unsupported type: <%s> = %s' % (type(v), v))


def dumb_decode(v, aes_key=None):
    if isinstance(v, bytes):
        if v[:1] == b' ': v = v.lstrip(b' ')
        if v[:1] == b'b': return v[1:]
        if v[:1] == b'B': return binascii.a2b_base64(v[1:])
        if v[:1] == b'u': return str(v[1:], 'utf-8')
        if v[:1] == b'U': return unquote(str(v[1:], 'latin-1'))
    else:
        if v[:1] == ' ': v = v.lstrip(' ')
        if v[:1] == 'b': return v[1:].encode('latin-1')
        if v[:1] == 'B': return binascii.a2b_base64(v[1:])
        if v[:1] == 'u': return str(v[1:].encode('latin-1'), 'utf-8')
        if v[:1] == 'U': return unquote(v[1:])

    if v[:1] in DUMB_DECODERS:
        return DUMB_DECODERS[v[:1]](v)

    if v[:1] in ('d', b'd'): return int(v[1:])
    if v[:1] in ('f', b'f'): return float(v[1:])
    if v in ('y', b'y'): return True
    if v in ('n', b'n'): return False
    if v in ('-', b'-'): return None

    if v[:1] in ('j', b'j'): return json.loads(v[1:])
    if v[:1] in ('J', b'J'): return json.loads(unquote_to_bytes(v[1:]))
    if v[:1] in ('s', b's'): return set(json.loads(v[1:]))
    if v[:1] in ('S', b'S'): return set(json.loads(unquote_to_bytes(v[1:])))
    if v[:1] in ('t', b't'): return tuple(json.loads(v[1:]))
    if v[:1] in ('T', b'T'): return tuple(json.loads(unquote_to_bytes(v[1:])))

    if v[:1] in ('Z', b'Z'):
        return dumb_decode(zlib.decompress(binascii.a2b_base64(v[1:])))
    if v[:1] == b'z':
        return dumb_decode(zlib.decompress(v[1:]))
    if v[:1] == 'z':
        return dumb_decode(zlib.decompress(v[1:].encode('latin-1')))

    if v[:1] in ('E', b'E'):
        v = b'e' + binascii.a2b_base64(v[1:])
    if v[:1] in ('e', b'e'):
        if aes_key is None:
            return v[1:17], v[17:]
        iv = v[1:17]
        return dumb_decode(aes_ctr_decrypt(aes_key, iv, v[17:]))

    return (v if isinstance(v, str) else str(v, 'utf-8'))


def register_dumb_decoder(char, func):
    global DUMB_DECODERS
    for ch in (char.upper(), char.lower()):
        DUMB_DECODERS[ch] = func
        DUMB_DECODERS[bytes(ch, 'latin-1')] = func


if __name__ == '__main__':
    assert(dumb_encode_bin(bytearray(b'1')) == b'b1')
    assert(dumb_encode_bin(None)            == b'-')
    assert(dumb_encode_bin({'hi':2})        == b'j{"hi":2}')

    assert(dumb_encode_asc(bytearray(b'1')) == 'BMQ==')
    assert(dumb_encode_asc(None)            == '-')
    assert(dumb_encode_asc({'hi':2})        == 'J%7B%22hi%22%3A2%7D')

    for i,o in (
        (b'b123\0', b'123\0'),
        (b'u123',    '123'),
        (b'u\xc3\x9eetta', 'Þetta'),
        (b'U%C3%9Eetta', 'Þetta')
    ):
        assert(dumb_decode(dumb_encode_bin(o)) == o)
        assert(dumb_decode(dumb_encode_asc(o)) == o)

        d = dumb_decode(i)
        if (d != o):
            print('dumb_decode(%s) == %s != %s' % (i, d, o))
            assert(False)

        d = dumb_decode(str(i, 'latin-1'))
        if (d != o):
            print('dumb_decode(%s) == %s != %s' % (i, d, o))
            assert(False)

    longish = ('1' * 1000)
    assert(len(dumb_encode_asc(longish, compress=10)) < len(longish))
    assert(dumb_decode(dumb_encode_asc(longish, compress=10)) == longish)
    assert(dumb_decode(dumb_encode_asc(longish, compress=10).encode('latin-1')) == longish)
    assert(dumb_decode(dumb_encode_bin(longish, compress=10)) == longish)
    assert(dumb_decode(str(dumb_encode_bin(longish, compress=10), 'latin-1')) == longish)

    iv = b'1234123412341234'
    key = make_aes_key(b'45674567')
    sec = 'hello encrypted world'
    enc_a = dumb_encode_asc(sec, aes_key_iv=(key, iv))
    enc_b = dumb_encode_bin(sec, aes_key_iv=(key, iv))
    assert(sec == dumb_decode(enc_a, aes_key=key))
    assert(sec == dumb_decode(enc_b, aes_key=key))

    print('Tests passed OK')
