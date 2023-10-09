import unittest
import doctest

import moggie.util.imap
#import moggie.util.conn_brokers
import moggie.util.mailpile
import moggie.util.friendly

from moggie.util.dumbcode import *


class DoctestTests(unittest.TestCase):
    def run_doctests(self, module):
        result = doctest.testmod(module)
        self.assertFalse(result.failed)

    def test_doctests_imap(self):
        self.run_doctests(moggie.util.imap)

#   def test_doctests_conn_brokers(self):
#       self.run_doctests(moggie.util.conn_brokers)

    def test_doctests_mailpile(self):
        self.run_doctests(moggie.util.mailpile)


class DummbCodeTests(unittest.TestCase):
    def test_dumbcode_bin(self):
        self.assertTrue(dumb_encode_bin(bytearray(b'1')) == b'b1')
        self.assertTrue(dumb_encode_bin(None)            == b'-')
        self.assertTrue(dumb_encode_bin({'hi':2})        == b'D3,2,uhid2')

    def test_dumbcode_asc(self):
        self.assertTrue(dumb_encode_asc(bytearray(b'1')) == 'BMQ==')
        self.assertTrue(dumb_encode_asc(None)            == '-')
        self.assertTrue(dumb_encode_asc({'hi':2})        == 'D3,2,Uhid2')

    def test_dumbcode_roundtrip(self):
        stuff = {b'hi':[3,4]}
        self.assertTrue(stuff == dumb_decode(dumb_encode_bin(stuff)))
        self.assertTrue(stuff == dumb_decode(dumb_encode_asc(stuff)))
        for i, o in (
            (b'b123\0', b'123\0'),
            (b'u123',    '123'),
            (b'u\xc3\x9eetta', 'Þetta'),
            (b'U%C3%9Eetta', 'Þetta')
        ):
            self.assertTrue(dumb_decode(dumb_encode_bin(o)) == o)
            self.assertTrue(dumb_decode(dumb_encode_asc(o)) == o)

            d = dumb_decode(i)
            if (d != o):
                print('dumb_decode(%s) == %s != %s' % (i, d, o))
                self.assertTrue(False)

            d = dumb_decode(str(i, 'latin-1'))
            if (d != o):
                print('dumb_decode(%s) == %s != %s' % (i, d, o))
                self.assertTrue(False)

    def test_dumbcode_longish(self):
        longish = ('1' * 1000)
        self.assertTrue(len(dumb_encode_asc(longish, compress=10)) < len(longish))
        self.assertTrue(dumb_decode(dumb_encode_asc(longish, compress=10)) == longish)
        self.assertTrue(dumb_decode(dumb_encode_asc(longish, compress=10).encode('latin-1')) == longish)
        self.assertTrue(dumb_decode(dumb_encode_bin(longish, compress=10)) == longish)
        self.assertTrue(dumb_decode(str(dumb_encode_bin(longish, compress=10), 'latin-1')) == longish)

    def test_dumbcode_crypto(self):
        from moggie.crypto.aes_utils import make_aes_key
        iv = b'1234123412341234'
        key = make_aes_key(b'45674567')
        sec = 'hello encrypted world'
        enc_a = dumb_encode_asc(sec, aes_key_iv=(key, iv))
        enc_b = dumb_encode_bin(sec, aes_key_iv=(key, iv))
        self.assertTrue(sec == dumb_decode(enc_a, aes_key=key))
        self.assertTrue(sec == dumb_decode(enc_b, aes_key=key))
