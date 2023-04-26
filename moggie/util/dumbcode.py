# Methods for serializing/deserializing common Python data formats, as
# well as moggie-specific things (this is pluggable).
#
# The methods dumb_encode_bin will generate a binary representation of
# the data, dumb_encode_asc will generate an ASCII (7bit) representation.
# Both can be decoded using dumb_decode.
#
# The to_json and from_json will use normal JSON encoding for data types
# which are natively common to both Python and JSON, and resorts to
# embedding dumb_encode_asc() output for binary data, sets, tuples and
# moggie-specific things.
#
# Choosing which to use:
#
#   - to_json and from_json are mostly compatible with the rest of the world
#   - to_json and from_json are fastest for common (cleartext) use cases
#   - dumb_encode_bin is the most compact when storing binary data
#   - dumb_encode_* support compression and encryption
#
# So as a rule of thumb, the dumb_encode_* methods get used internally,
# but we'll use JSON any time we expect to expose our data to the outside
# word.
#
import binascii
import json
import logging
import zlib

from urllib.parse import quote, unquote, unquote_to_bytes

from ..crypto.aes_utils import aes_ctr_encrypt, aes_ctr_decrypt, make_aes_key


DUMB_DECODERS = {}


def dumb_encode_bin(v,
        compress=False, comp_bin=(b'z', zlib.compress), comp_asc=None,
        aes_key_iv=None):

    if aes_key_iv:
        key, iv = aes_key_iv
        assert(len(iv) == 16)
        encoded = dumb_encode_bin(v, compress=compress, comp_bin=comp_bin)
        encrypted = aes_ctr_encrypt(key, iv, encoded)
        return b'e' + iv + encrypted

    if compress:
        encoded = dumb_encode_bin(v, compress=False)
        if len(encoded) > compress:
            marker, compressor = comp_bin
            compressed = compressor(encoded)
            saved = len(encoded) - len(compressed)
            if saved > 10:
                return marker + compressed
        return encoded

    if isinstance(v, bytes):     return (b'b' + v)
    if isinstance(v, str):       return (b'u' + v.encode('utf-8'))
    if isinstance(v, bool):      return (b'y' if v else b'n')
    if isinstance(v, int):       return (b'd%d' % v)
    if isinstance(v, float):     return (b'f%f' % v)
    if isinstance(v, bytearray): return (b'b' + bytes(v))
    if v is None:                return (b'-')

    if hasattr(v, 'dumb_encode_bin'): return v.dumb_encode_bin()

    if isinstance(v, (list, tuple, set)):
        items = [
            b'L' if isinstance(v, list) else (
            b'T' if isinstance(v, tuple) else b'S')]
        for elem in v:
            e = dumb_encode_bin(elem)
            items.append(b'%x,%s' % (len(e), e))
        return b''.join(items)

    if isinstance(v, dict):
        items = [b'D']
        for key, val in v.items():
            key = dumb_encode_bin(key)
            val = dumb_encode_bin(val)
            items.append(b'%x,%x,%s%s' % (len(key), len(val), key, val))
        return b''.join(items)

    raise ValueError('Unsupported type: <%s> = %s' % (type(v), v))


def dumb_encode_asc(v,
        compress=False, comp_bin=None, comp_asc=('Z', zlib),
        aes_key_iv=None):

    if aes_key_iv:
        key, iv = aes_key_iv
        assert(len(iv) == 16)
        encoded = dumb_encode_bin(v, compress=compress, comp_asc=comp_asc)
        encrypted = aes_ctr_encrypt(key, iv, encoded)
        return 'E' + str(binascii.b2a_base64(iv + encrypted, newline=False), 'latin-1')

    try:
        if compress and len(v) > compress:
            marker, compressor = comp_asc
            compressed = str(binascii.b2a_base64(
                compressor.compress(dumb_encode_bin(v, compress=False)),
                newline=False), 'latin-1')
            if len(compressed) < len(v):
                return marker + compressed
    except TypeError:
        pass

    if isinstance(v, bytes):     return ('B' + str(binascii.b2a_base64(v, newline=False), 'latin-1'))
    if isinstance(v, str):       return ('U' + quote(v, safe='').replace('.', '%2E'))
    if isinstance(v, bool):      return ('y' if v else 'n')
    if isinstance(v, int):       return ('d%d' % v)
    if isinstance(v, float):     return ('f%f' % v)
    if isinstance(v, bytearray): return ('B' + str(binascii.b2a_base64(v, newline=False), 'latin-1'))
    if v is None:                return ('-')

    if hasattr(v, 'dumb_encode_asc'): return v.dumb_encode_asc()

    if isinstance(v, (list, tuple, set)):
        items = [
                'L' if isinstance(v, list) else (
                'T' if isinstance(v, tuple) else 'S')
            ] * (len(v)+1)  # Preallocating the list is faster
        i = 0
        for elem in v:
            e = dumb_encode_asc(elem)
            i += 1
            items[i] = ('%x,%s' % (len(e), e))
        return ''.join(items)

    if isinstance(v, dict):
        items = ['D'] * (len(v) + 1)  # Preallocating the list is faster
        i = 0
        for key, val in v.items():
            key = dumb_encode_asc(key)
            val = dumb_encode_asc(val)
            i += 1
            items[i] = ('%x,%x,%s%s' % (len(key), len(val), key, val))
        return ''.join(items)

    raise ValueError('Unsupported type: <%s> = %s' % (type(v), v))


def dumb_decode_dict(v):
    dct = {}
    while v:
        l1, l2, v = v.split(',', 2)
        l1 = int(l1, 16)
        l2 = int(l2, 16) + l1
        key = dumb_decode(v[:l1])
        val = dumb_decode(v[l1:l2])
        dct[key] = val
        v = v[l2:]
    return dct


def dumb_decode_list(v):
    lst = []
    while v:
        l1, v = v.split(',', 1)
        l1 = int(l1, 16)
        lst.append(dumb_decode(v[:l1]))
        v = v[l1:]
    return lst


def dumb_decode(v,
        aes_key=None, iv_to_aes_key=None,
        decomp_asc=[], decomp_bin=[]):

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

    if v[:1] in ('d', b'd'): return int(v[1:])
    if v[:1] in ('f', b'f'): return float(v[1:])
    if v     in ('y', b'y'): return True
    if v     in ('n', b'n'): return False
    if v     in ('-', b'-'): return None

    if v[:1] in DUMB_DECODERS:
        return DUMB_DECODERS[v[:1]](v)

    if v[:1] == 'D': return dumb_decode_dict(v[1:])
    if v[:1] == b'D': return dumb_decode_dict(str(v[1:], 'latin-1'))
    if v[:1] == 'L': return dumb_decode_list(v[1:])
    if v[:1] == b'L': return dumb_decode_list(str(v[1:], 'latin-1'))
    if v[:1] == 'S': return set(dumb_decode_list(v[1:]))
    if v[:1] == b'S': return set(dumb_decode_list(str(v[1:], 'latin-1')))
    if v[:1] == 'T': return tuple(dumb_decode_list(v[1:]))
    if v[:1] == b'T': return tuple(dumb_decode_list(str(v[1:], 'latin-1')))

    if v[:1] in ('j', b'j'): return json.loads(v[1:])
    if v[:1] in ('J', b'J'): return json.loads(unquote_to_bytes(v[1:]))

    for ms, mb, decomp in ([('Z', b'Z', zlib.decompress)] + decomp_asc):
        if v[:1] in (ms, mb):
            return dumb_decode(decomp(binascii.a2b_base64(v[1:])))

    for ms, mb, decomp in ([('z', b'z', zlib.decompress)] + decomp_bin):
        if v[:1] == mb:
            return dumb_decode(decomp(v[1:]))
        if v[:1] == ms:
            return dumb_decode(decomp(v[1:].encode('latin-1')))

    if v[:1] in ('E', b'E'):
        v = b'e' + binascii.a2b_base64(v[1:])
    if v[:1] in ('e', b'e'):
        iv, data = v[1:17], v[17:]
        if iv_to_aes_key is not None:
            aes_key = iv_to_aes_key(iv)
        if aes_key is None:
            return iv, data
        return dumb_decode(aes_ctr_decrypt(aes_key, iv, data),
             decomp_asc=decomp_asc,
             decomp_bin=decomp_bin)

    try:
        return (v if isinstance(v, str) else str(v, 'utf-8'))
    except:
        logging.exception(
            'BOGUS: %s (decomps=%s/%s)' % (v, decomp_bin, decomp_asc))
        raise


def dumb_json_encoder(obj):
    try:
        logging.debug('BUG? Encoding %s into JSON' % obj.__class__.__name__)
        try:
            # We use this structure to identify our encoded objects:
            #
            # List with exactly three items, the first of which is the value
            # -76, the second is our data encoded as a string, the third is
            # (length of the encoded string) - 76. This combination of silly
            # characteristics should be pretty rare in the wild.
            #
            # This should make the odds of "accidental" decoding low, but
            # also makes it harder for attackers to inject malicious data.
            enc_obj = dumb_encode_asc(obj)
            return [-76, enc_obj, len(enc_obj) - 76]
        except ValueError:
            if hasattr(obj, '__iter__'):
                return list(obj)
    except (ValueError, TypeError):
        raise TypeError('Cannot JSON serialize %s' % obj.__class__.__name__)


def dumb_json_decoder(obj):
    """
    Recursively iterate through lists and dicts, decoding strings that
    have our magic marker, but returning other objects unchanged.
    """
    if isinstance(obj, dict):
        for k in obj:
            obj[k] = dumb_json_decoder(obj[k])
    elif isinstance(obj, list):
        # Is it our magic list structure?
        if ((len(obj) == 3)
                and (obj[0] == -76)
                and isinstance(obj[1], str)
                and (obj[2] == len(obj[1]) - 76)):
            obj = dumb_decode(obj[1])
        else:
            for i, v in enumerate(obj):
                obj[i] = dumb_json_decoder(v)
    return obj


def to_json(data, indent=None):
    return json.dumps(data,
        separators=(',', ':'), indent=indent,
        default=dumb_json_encoder)


def from_json(data, dumb_decode=True):
    if dumb_decode:
        return dumb_json_decoder(json.loads(data))
    else:
        return json.loads(data)


def register_dumb_decoder(char, func):
    global DUMB_DECODERS
    for ch in (char.upper(), char.lower()):
        DUMB_DECODERS[ch] = func
        DUMB_DECODERS[bytes(ch, 'latin-1')] = func


if __name__ == '__main__':
    import time

    assert(dumb_encode_bin(bytearray(b'1')) == b'b1')
    assert(dumb_encode_bin(None)            == b'-')
    assert(dumb_encode_bin({'hi':2})        == b'D3,2,uhid2')

    assert(dumb_encode_asc(bytearray(b'1')) == 'BMQ==')
    assert(dumb_encode_asc(None)            == '-')
    assert(dumb_encode_asc({'hi':2})        == 'D3,2,Uhid2')

    assert(dumb_decode(dumb_encode_bin({b'hi':[3,4]})) == {b'hi':[3,4]})
    assert(dumb_decode(dumb_encode_asc({b'hi':[3,4]})) == {b'hi':[3,4]})

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

    if False:
        from ..storage.metadata import METADATA_ZDICT
        # This was used to measure whether using the compressobj with a
        # dictionary would slow us down or not. It seems fine!
        foo = zlib.compressobj(zdict=METADATA_ZDICT)
        blob = METADATA_ZDICT + METADATA_ZDICT
        c0, t0 = 0, time.time()
        for i in range(0, 100000):
            c0 += len(foo.copy().compress(blob))
        c1, t1 = 0, time.time()
        for i in range(0, 100000):
            c1 += len(zlib.compress(blob))
        t2 = time.time()
        print('%2.2fs %d bytes vs. %2.2fs %d bytes'
            % (t2-t1, c1, t1-t0, c0))

    print('Tests passed OK')
