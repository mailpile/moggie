import json
import zlib

from urllib.parse import quote, unquote, unquote_to_bytes


def dumb_encode_bin(v, compress=False):
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


def dumb_encode_asc(v, compress=False):
    try:
        if compress and len(v) > compress:
            compressed = quote(zlib.compress(dumb_encode_bin(v, compress=False)))
            if len(compressed) < len(v):
                return 'Z' + compressed
    except TypeError:
        pass

    if isinstance(v, bytes):     return ('B' + quote(v))
    if isinstance(v, str):       return ('U' + quote(v))
    if isinstance(v, bool):      return ('y' if v else 'n')
    if isinstance(v, int):       return ('d%d' % v)
    if isinstance(v, float):     return ('f%f' % v)
    if isinstance(v, bytearray): return ('B' + quote(bytes(v)))
    if v is None:                return ('-')

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


def dumb_decode(v):
    if isinstance(v, bytes):
        if v[:1] == b'b': return v[1:]
        if v[:1] == b'B': return unquote_to_bytes(v[1:])
        if v[:1] == b'u': return str(v[1:], 'utf-8')
        if v[:1] == b'U': return unquote(str(v[1:], 'latin-1'))
    else:
        if v[:1] == 'b': return v[1:].encode('latin-1')
        if v[:1] == 'B': return unquote_to_bytes(v[1:])
        if v[:1] == 'u': return str(v[1:].encode('latin-1'), 'utf-8')
        if v[:1] == 'U': return unquote(v[1:])

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
        return dumb_decode(zlib.decompress(unquote_to_bytes(v[1:])))
    if v[:1] == b'z':
        return dumb_decode(zlib.decompress(v[1:]))
    if v[:1] == 'z':
        return dumb_decode(zlib.decompress(v[1:].encode('latin-1')))

    return (v if isinstance(v, str) else str(v, 'utf-8'))


if __name__ == '__main__':
    print('%s' % dumb_encode_bin('Þetta'))

    assert(dumb_encode_bin(bytearray(b'1')) == b'b1')
    assert(dumb_encode_bin(None)            == b'-')
    assert(dumb_encode_bin({'hi':2})        == b'j{"hi":2}')

    assert(dumb_encode_asc(bytearray(b'1')) == 'B1')
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

    print('Tests passed OK')
