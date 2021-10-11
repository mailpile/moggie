import copy
import binascii
import numpy

from .dumbcode import register_dumb_decoder


class IntSet:
    ENC_BIN = b'i'
    ENC_ASC = 'I'

    # We could change these to match the CPU we are running on, but
    # doing so would make our data files non-portable. File portability
    # is why we are explicit about endianness here.
    DEF_BITS = 64
    DEF_DTYPE = numpy.dtype('<u8')

    DEF_INIT = 64
    DEF_GROW = 1024

    def __init__(self,
            copy=None, clone=None, binary=None, init=DEF_INIT,
            bits=DEF_BITS, dtype=DEF_DTYPE):
        self.bits = bits
        self.dtype = dtype

        if clone is not None:
            self.npa = clone.npa
            self.bits = clone.bits
            self.dtype = clone.dtype

        elif copy is not None:
            if isinstance(copy, IntSet):
                self.npa = numpy.copy(copy.npa)
                self.bits = copy.bits
                self.dtype = copy.dtype
            else:
                self.npa = numpy.zeros(init, dtype=self.dtype)
                self |= copy

        elif binary is not None:
            self.frombinary(binary)

        elif init:
            self.npa = numpy.zeros(init or 1, dtype=self.dtype)

        else:
            self.npa = None

        self.maxint = 0
        for bit in range(0, self.bits):
            self.maxint |= (1 << bit)

    @classmethod
    def All(cls, count):
        iset = cls(init=None)

        maxpos = count // iset.bits
        iset.npa = numpy.invert(
            numpy.zeros(1 + maxpos, dtype=iset.dtype),
            dtype=iset.dtype)

        mask = 0
        for i in range(iset.bits * maxpos, count):
            mask |= (1 << (i % iset.bits))
        iset.npa[maxpos] = int(iset.npa[maxpos]) & mask

        return iset

    @classmethod
    def Sub(cls, *sets, clone=False):
        if clone:
            result = cls(clone=sets[0])
        else:
            result = cls(copy=sets[0])
        for s in sets[1:]:
            result -= s
        return result

    @classmethod
    def And(cls, *sets, clone=False):
        if clone:
            result = cls(clone=sets[0])
        else:
            result = cls(copy=sets[0])
        for s in sets[1:]:
            result &= s
        return result

    @classmethod
    def Or(cls, *sets, clone=False):
        if clone:
            result = cls(clone=sets[0])
        else:
            result = cls(copy=sets[0])
        for s in sets[1:]:
            result |= s
        return result

    @classmethod
    def DumbDecode(cls, encoded):
        if encoded[:1] in ('i', b'i'):
            binary = encoded[1:]
        elif encoded[:1] in ('I', b'I'):
            binary = binascii.a2b_base64(encoded[1:])
        else:
            raise ValueError('Invalid IntSet encoding')
        return cls().frombytes(binary)

    def dumb_encode_bin(self):
        return self.ENC_BIN + self.tobytes()

    def dumb_encode_asc(self):
        return self.ENC_ASC + str(
            binascii.b2a_base64(self.tobytes(), newline=False),
            'latin-1')

    def frombytes(self, binary):
        self.npa = numpy.copy(numpy.frombuffer(binary, dtype=self.dtype))
        return self

    def tobytes(self):
        return self.npa.tostring()

    def __len__(self):
        # Estimate how large a naive binary encoding will be:
        # 8 bytes per 64-bit int. This is used by dumb_encode to
        # decide whether to compress or not.
        return len(self.npa) * (self.bits // 8)

    def __contains__(self, val):
        pos = val // self.bits
        if pos >= len(self.npa):
            return False
        bit = val % self.bits
        return (int(self.npa[pos]) & (1 << bit))

    def __isub__(self, other):
        if isinstance(other, IntSet):
            maxlen = min(len(self.npa), len(other.npa))
            self.npa[:maxlen] &= numpy.invert(
                other.npa[:maxlen], dtype=self.dtype)

        elif isinstance(other, int):
            val = other
            pos = val // self.bits
            if pos < len(self.npa):
                bitset = 1 << (val % self.bits)
                bitnot = self.maxint - bitset
                self.npa[pos] = int(self.npa[pos]) & bitnot

        elif isinstance(other, (list, tuple, set)):
            if len(other) > 0:
                self -= IntSet(other)
        else:
            raise ValueError('Bad type %s' % type(other))

        return self

    def __iand__(self, other):
        if isinstance(other, IntSet):
            maxlen = min(len(self.npa), len(other.npa))
            self.npa[:maxlen] &= other.npa[:maxlen]
            if maxlen < len(self.npa):
                self.npa[maxlen:] = numpy.zeros(len(self.npa) - maxlen, dtype=self.dtype)

        elif isinstance(other, (list, tuple, set)):
            if len(other) > 0:
                self &= IntSet(other)
        else:
            raise ValueError('Bad type %s' % type(other))
        return self

    def __ior__(self, other):
        if isinstance(other, IntSet):
            if len(other.npa) > len(self.npa):
                self.npa.resize(len(other.npa) + self.DEF_GROW)
            self.npa[:len(other.npa)] |= other.npa

        elif isinstance(other, int):
            val = other
            pos = val // self.bits
            if pos > len(self.npa):
                self.npa.resize(pos + self.DEF_GROW)
            bit = val % self.bits
            self.npa[pos] = int(self.npa[pos]) | (1 << bit)

        elif isinstance(other, (tuple, list, set)):
            if len(other) > 0:
                maxint = max(other)
                bitmask = [0] * (1 + (maxint // self.bits))
                for i in other:
                    bitmask[i // self.bits] |= (1 << (i % self.bits))
                if len(bitmask) > len(self.npa):
                    self.npa.resize(len(bitmask) + self.DEF_GROW)

                self.npa[:len(bitmask)] |= numpy.array(bitmask, dtype=self.dtype)
        else:
            raise ValueError('Bad type %s' % type(other))
        return self

    def chunks(self, size=1024, reverse=True):
        result = []
        if reverse:
            rrange = lambda a,b: reversed(range(a, b))
        else:
            rrange = lambda a,b: range(a, b)
        for i in rrange(0, len(self.npa)):
            u64 = int(self.npa[i])
            if u64:
                for j in rrange(0, self.bits):
                    if (u64 & (1 << j)):
                        result.append((i * self.bits) + j)
            while len(result) >= size:
                yield result[:size]
                result = result[size:]
        if result:
            yield result

    def __iter__(self):
        for i in range(0, len(self.npa)):
            u64 = int(self.npa[i])
            if u64:
                for j in range(0, self.bits):
                    if (u64 & (1 << j)):
                        yield (i * self.bits) + j


register_dumb_decoder(IntSet.ENC_ASC, IntSet.DumbDecode)


if __name__ == "__main__":
    import time
    from ..util.dumbcode import *

    assert(IntSet.DEF_BITS == 64)

    is1 = IntSet([1, 3, 10])
    assert(10 in is1)
    assert(4 not in is1)
    assert(1024 not in is1)
    assert(10 in list(is1))
    assert(11 not in list(is1))
    is1 |= 11
    assert(10 in list(is1))
    assert(11 in list(is1))
    is1 &= [1, 3, 9, 44]
    assert(3 in list(is1))
    is1 -= 9
    assert(9 not in is1)
    is1 |= 9
    assert(9 in is1)
    is1 -= [9]
    assert(9 not in is1)
    assert(11 not in list(is1))
    assert(len(is1.tobytes()) == (is1.DEF_INIT * is1.bits // 8))

    a100 = IntSet.All(100)
    assert(99 in a100)
    assert(100 not in a100)
    assert(len(list(a100)) == 100)
    assert(list(IntSet.Sub(a100, IntSet.All(99))) == [99])
    a100 -= 99
    assert(98 in a100)
    assert(99 not in a100)
    assert(0 in a100)

    e_is1 = dumb_encode_asc(is1, compress=128)
    d_is1 = dumb_decode(e_is1)
    #print('%s' % e_is1)
    assert(len(e_is1) < 1024)
    assert(list(d_is1) == list(is1))
    e_is1 = dumb_encode_bin(is1)
    d_is1 = dumb_decode(e_is1)
    assert(list(d_is1) == list(is1))

    #print('%s' % list(is1))
    #for i in is1.chunks(size=1, reverse=False):
    #    print('%s' % list(i))
    #for i in is1.chunks(size=1, reverse=True):
    #    print('%s' % list(i))

    many = list(range(0, 10240000, 10))
    some = list(range(0, 1024000, 10))
    few = [0, 1020, 9990, 1024000-10]

    t0 = time.time()
    count = 10

    print('Tests passed OK')

    for i in range(0, count):
        b1 = IntSet(many)
        b2 = IntSet(some)
        b3 = IntSet(few)
    t1 = time.time()
    assert(len(b1.npa) == b1.DEF_GROW + 10 * len(many) // b1.bits)
    assert(len(b1.tobytes()) == b1.bits * (b1.DEF_GROW + 10*len(many) // b1.bits) // 8)
    print(' * ints_to_bitmask x %d = %.2fs' % (3 * count, t1-t0))
    t1 = time.time()

    for i in range(0, 100*count):
        b4 = IntSet.And(b1, b1, b1)
        b4 = IntSet.And(b1, b2, b3)
    t2 = time.time()
    assert(list(b4) == list(b3))
    print(' * bitmask_and x %d   = %.2fs' % (200 * count, t2-t1))
    t2 = time.time()

    for i in range(0, 100*count):
        b5 = IntSet.Or(b1, b1, b1)
        b5 = IntSet.Or(b1, b2, b3)
    t3 = time.time()
    assert(list(b5) == list(b1))
    print(' * bitmask_or x %d    = %.2fs' % (200 * count, t3-t2))
    t3 = time.time()

    for i in range(0, count):
        l1 = list(b1)
        l2 = list(b2)
        l3 = list(b3)
    t4 = time.time()
    assert(list(l1) == many)
    assert(list(l2) == some)
    assert(list(l3) == few)
    print(' * bitmask_to_ints x %d = %.2fs' % (3 * count, t4-t3))
    t4 = time.time()

