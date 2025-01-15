import unittest
import doctest

#import moggie.util.conn_brokers
import moggie.util.http
import moggie.util.imap
import moggie.util.intset
import moggie.util.mailpile
import moggie.util.sendmail

from moggie.util.dumbcode import *
from moggie.util.friendly import *
from moggie.util.intset import IntSet
from moggie.util.wordblob import *
from moggie.util.sendmail import *


class DoctestTests(unittest.TestCase):
    def run_doctests(self, module):
        results = doctest.testmod(module)
        if results.failed:
            print(results)
        self.assertFalse(results.failed)

    def test_doctests_imap(self):
        self.run_doctests(moggie.util.imap)

#   def test_doctests_conn_brokers(self):
#       self.run_doctests(moggie.util.conn_brokers)

    def test_doctests_mailpile(self):
        self.run_doctests(moggie.util.mailpile)


class DummbCodeTests(unittest.TestCase):
    def test_dumbcode_bin(self):
        self.assertTrue(dumb_encode_bin(None)            == b'-')
        self.assertTrue(dumb_encode_bin(bytearray(b'1')) == b'b1')
        self.assertTrue(dumb_encode_bin({'hi':2})[:1]    == b'p')

    def test_dumbcode_asc(self):
        self.assertTrue(dumb_encode_asc(None)            == '-')
        self.assertTrue(dumb_encode_asc(bytearray(b'1')) == 'BMQ==')
        self.assertTrue(dumb_encode_asc({'hi':2})        == 'D3,2,Uhid2')

    def test_dumbcode_roundtrip(self):
        stuff = {b'hi': [3, 4, True, False, 'alphabet']}
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

    def test_dumbcode_to_json(self):
        import json
        for thing in (
                True, False, 1, "hello",
                [1, 2, 3],
                {'hi': [1, 2], 'hello': 'world'}):
            self.assertEqual(json.loads(to_json(thing)), thing)

    def test_dumbcode_from_json(self):
        for thing in (
                True, False, 1, "hello",
                [1, 2, 3],
                {'hi': [1, 2], 'hello': 'world'}):
            self.assertEqual(from_json(json.dumps(thing)), thing)

    def test_dumbcode_json_binary(self):
        for thing in (
                b'binary stuff',
                IntSet([1, 2, 3, 4])):
            self.assertEqual(from_json(to_json(thing)), thing)


class FriendlyTests(unittest.TestCase):
    def test_secs_to_friendly_time(self):
        self.assertEqual(seconds_to_friendly_time(60, parts=2), '1M')
        self.assertEqual(seconds_to_friendly_time(120, parts=2), '2M')
        self.assertEqual(seconds_to_friendly_time(3721, parts=2), '1H 2M')
        self.assertEqual(seconds_to_friendly_time(3721, parts=3), '1H 2M') # No seconds!
        self.assertEqual(seconds_to_friendly_time(86400, parts=2), '1d')
        self.assertEqual(seconds_to_friendly_time(86401, parts=2), '1d') # No seconds!
        self.assertEqual(seconds_to_friendly_time(90000, parts=2), '1d 1H')

    def test_friendly_time_ago(self):
        ts = 1710849600  # 19 mar 2024, 12:00 UTC
        testing = friendly_time_ago_to_timestamp
        self.assertEqual(testing('0', now=ts), ts)
        self.assertEqual(testing('1h', now=ts), ts - 3600)
        self.assertEqual(testing('1d', now=ts), ts - (24*3600))
        self.assertEqual(testing('1w', now=ts), ts - (7*24*3600))
        self.assertEqual(testing('1m', now=ts), ts - (29*24*3600))
        self.assertEqual(testing('1y', now=ts), 1679227200)  # 19 mar 2023, 12:00 UTC
        self.assertEqual(testing('12m', now=ts), 1679227200)  # 19 mar 2023, 12:00 UTC
        self.assertEqual(testing('16m', now=ts), 1668859200)  # 19 nov 2022, 12:00 UTC
        self.assertEqual(testing('10y', now=ts), 1395230400)  # 19 mar 2014, 12:00 UTC

    def test_friendly_date(self):
        ts = 1696845600
        self.assertEqual(friendly_date(ts), '2023-10-09')
        self.assertEqual(friendly_date(str(ts)), '2023-10-09')
        self.assertEqual(friendly_date(None), '?')

    def test_friendly_datetime(self):
        ts = 1696845623
        self.assertEqual(friendly_datetime(ts), '2023-10-09 10:00')
        self.assertEqual(friendly_datetime(str(ts)), '2023-10-09 10:00')
        self.assertEqual(friendly_datetime(None), '?')

    def test_friendly_bytes(self):
        self.assertEqual(friendly_bytes(1), '1')
        self.assertEqual(friendly_bytes(1100), '1K')
        self.assertEqual(friendly_bytes(110*1024), '110K')
        self.assertEqual(friendly_bytes(11*1024*1024), '11M')
        self.assertEqual(friendly_bytes(11*1024*1024*1024), '11G')
        self.assertEqual(friendly_bytes(None), '?')

    def test_friendly_date_formats(self):
        ts = 1696845623
        fmts = friendly_date_formats(ts)
        self.assertEquals(fmts['yyyy_mm_dd'], '2023-10-09')
        self.assertEquals(fmts['yyyy_mm'], '2023-10')
        self.assertEquals(fmts['yyyy'], '2023')
        self.assertEquals(fmts['ts'], ts)


class HttpTests(unittest.TestCase):
    def test_url_parts(self):
        for url, parse in (
                ('http://example.org/foo',   ('http', 'example.org', 80, '/foo')),
                ('http://user@example.org',  ('http', 'example.org', 80, '/')),
                ('https://example.org',      ('https', 'example.org', 443, '/')),
                ('https://example.org:123/', ('https', 'example.org', 123, '/')),
                ('https://example.org:123/', ('https', 'example.org', 123, '/')),
                ):
            self.assertEqual(moggie.util.http.url_parts(url), parse)


class SendmailTests(unittest.TestCase):
    def test_partial_url(self):
        parse_partial_url = moggie.util.sendmail.parse_partial_url
        self.assertEqual(
            parse_partial_url('http://user:pass@host:443/path/to/stuff'),
            ('http', 'user', 'pass', 'host', 443, 'path/to/stuff'))
        self.assertEqual(
            parse_partial_url('user@host/path/to/stuff'),
            ('smtp', 'user', None, 'host', 25, 'path/to/stuff'))
        self.assertEqual(
            parse_partial_url('localhost:125'),
            ('smtp', None, None, 'localhost', 125, None))
        self.assertEqual(
            parse_partial_url('user:secret@localhost:125'),
            ('smtp', 'user', 'secret', 'localhost', 125, None))


class IntsetTest(unittest.TestCase):
    def test_intset(self):
        self.assertEqual(IntSet.DEF_BITS, 64)

        is1 = IntSet([1, 3, 10])
        self.assertTrue(10 in is1)
        self.assertTrue(4 not in is1)
        self.assertTrue(1024 not in is1)
        self.assertTrue(10 in list(is1))
        self.assertTrue(11 not in list(is1))
        is1 |= 11
        self.assertTrue(10 in list(is1))
        self.assertTrue(11 in list(is1))
        is1 &= [1, 3, 9, 44]
        self.assertTrue(3 in list(is1))
        is1 -= 9
        self.assertTrue(9 not in is1)
        is1 |= 9
        self.assertTrue(9 in is1)
        is1 -= [9]
        self.assertTrue(9 not in is1)
        self.assertTrue(11 not in list(is1))
        self.assertTrue(len(is1.tobytes(strip=False)) == (5 + is1.DEF_INIT * is1.bits // 8))
        is1 ^= [9, 44, 45, 46]
        self.assertTrue(9 in is1)
        self.assertTrue(46 in is1)
        self.assertTrue(47 not in is1)
        is1 ^= [9, 11]
        self.assertTrue(9 not in is1)
        self.assertTrue(11 in is1)

        a100 = IntSet.All(100)
        self.assertTrue(bool(a100))
        self.assertTrue(99 in a100)
        self.assertTrue(100 not in a100)
        self.assertTrue(len(list(a100)) == 100)
        self.assertTrue(list(IntSet.Sub(a100, IntSet.All(99))) == [99])
        a100 -= 99
        self.assertTrue(98 in a100)
        self.assertTrue(99 not in a100)
        self.assertTrue(0 in a100)

        e_is1 = dumb_encode_asc(is1, compress=128)
        d_is1 = dumb_decode(e_is1)
        #print('%s' % e_is1)
        self.assertTrue(len(e_is1) < 1024)
        self.assertTrue(list(d_is1) == list(is1))
        e_is1 = dumb_encode_bin(is1)
        d_is1 = dumb_decode(e_is1)
        self.assertTrue(list(d_is1) == list(is1))


class WordblobTest(unittest.TestCase):
    def test_wordblob(self):
        blob = create_wordblob([bytes(w, 'utf-8') for w in [
                'hello', 'world', 'this', 'is', 'great', 'oh', 'yeah',
                'thislongwordgetsignored'
            ]],
            shortest=2,
            longest=5,
            maxlen=20)

        # The noop is to just return the keyword itself!
        self.assertEqual(wordblob_search('bjarni', b'', 10), ['bjarni'])
        self.assertEqual(wordblob_search('bja*rni', b'', 10), ['bjarni'])

        # Searches...
        self.assertEqual(wordblob_search('*', blob, 10), [])
        self.assertEqual(wordblob_search('*****', blob, 10), [])
        self.assertEqual(wordblob_search('worl*', blob, 10), ['worl', 'world'])
        self.assertEqual(wordblob_search('*orld', blob, 10), ['orld', 'world'])
        self.assertEqual(wordblob_search('*at', blob, 10), ['at', 'great'])
        self.assertEqual(wordblob_search('w*d', blob, 10), ['wd', 'world'])
        self.assertEqual(wordblob_search('*w*r*d*', blob, 10), ['wrd', 'world'])

        # Test the LRU updates and blob searches which roughly preserve the
        # order within the blob (so we get more recent matches firstish).
        b1 = create_wordblob(b'five four three two one'.split(), shortest=1)
        b1 = update_wordblob([b'five'], b1, shortest=1, lru=True)
        b1 = update_wordblob([b'four'], b1, shortest=1, lru=True)
        b1 = update_wordblob([b'three'], b1, shortest=1, lru=True)
        b1 = update_wordblob([b'two'], b1, shortest=1, lru=True)
        b1 = update_wordblob([b'one'], b1, blacklist=[b'three'], shortest=1, lru=True)
        self.assertEqual(b1, b'one\ntwo\nfour\nfive')
        b1 = b'One\nTwo\nThree\nFour\nFive'
        self.assertEqual(wordblob_search('f*', b1, 10, order=-1), ['f', 'Five', 'Four'])
        self.assertEqual(wordblob_search('f*', b1, 10, order=+1), ['f', 'Four', 'Five'])


class ServerAndSenderTests(unittest.TestCase):
    def test_sas_parser(self):
        N = None
        for spec, proto, host, port, usr, pwd in (
            ('[::1]',                  'smtp',    '[::1]',        25, N, N),
            ('127.0.0.1:123',          'smtp',    '127.0.0.1',   123, N, N),
            ('smtps://127.0.0.1:123/', 'smtps',   '127.0.0.1',   123, N, N),
            ('example.org:123',        'smtp',    'example.org', 123, N, N),
            ('smtpclr://example.org/', 'smtpclr', 'example.org',  25, N, N),
            ('example.org',            'smtp',    'example.org',  25, N, N),
            ('smtps:u@127.0.0.1:123',  'smtps',   '127.0.0.1',   123, 'u', ''),
            ('u@h:p@ss@ex.org:44',     'smtp',    'ex.org', 44, 'u@h', 'p@ss'),
            ('u@h:p@ss@[::1]:22',      'smtp',    '[::1]',  22, 'u@h', 'p@ss'),
        ):
            sas = ServerAndSender().parse_server_spec(spec)

            self.assertEquals(sas.host, host)
            self.assertEquals(sas.port, port)
            self.assertEquals(sas.username_and_password(), (usr, pwd))
