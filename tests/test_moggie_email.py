import base64
import doctest
import unittest

import moggie.email.addresses
import moggie.email.headers
import moggie.email.parsemime
import moggie.email.sync
import moggie.email.util

from moggie.email.rfc2074 import *


class DoctestTests(unittest.TestCase):
    def run_doctests(self, module):
        results = doctest.testmod(module)
        if results.failed:
            print(results)
        self.assertFalse(results.failed)

    def test_doctests_addresses(self):
        self.run_doctests(moggie.email.addresses)

    def test_doctests_sync(self):
        self.run_doctests(moggie.email.sync)

    def test_doctests_util(self):
        self.run_doctests(moggie.email.util)

    def test_doctests_rfc2074(self):
        self.run_doctests(moggie.email.util)


class HeaderFormattingTests(unittest.TestCase):
    E1 = """\
Subject: Hello world
From: "Bjarni R. Einarsson" <bre@example.org>
To: "Somebody" <somebody@example.org>

""".replace('\n', '\r\n')

    E2 = """\
"""

    def test_format_headers(self):
        self.assertEquals(self.E1, moggie.email.headers.format_headers({
            'from': {'address': 'bre@example.org', 'fn': 'Bjarni R. Einarsson'},
            'to': [{'address': 'somebody@example.org', 'fn': 'Somebody'}],
            'subject': 'Hello world'}))


class RFC2704Tests(unittest.TestCase):
    def test_tests_from_RFC2074(self):
        self.assertEquals(rfc2074_unquote('=?ISO-8859-1?Q?a?= b'), 'a b')
        self.assertEquals(rfc2074_unquote('=?ISO-8859-1?Q?a?=  =?ISO-8859-1?Q?b?='), 'ab')
        self.assertEquals(rfc2074_unquote('=?ISO-8859-1?Q?a_b?='), 'a b')
        self.assertEquals(rfc2074_unquote('=?ISO-8859-1?Q?a?= =?ISO-8859-2?Q?_b?='), 'a b')

    def test_encoding_hello_verold(self):
        test = 'hello verööld'
        test_b64 = '=?utf-8?b?%s?=' % str(
           base64.b64encode(test.encode('utf-8')).strip(), 'latin-1')

        self.assertEquals(rfc2074_unquote('hello =?iso-8859-1?q?ver=F6=F6ld?='), test)
        self.assertEquals(rfc2074_unquote(test), test)
        self.assertEquals(rfc2074_unquote(test_b64), test)

    def test_invalid_utf8(self):
        # Make sure we do not explode on invalid UTF-8
        bad_b64 = str(base64.b64encode(b'\xc3\0\0\0').strip(), 'latin-1')
        self.assertEquals(
            rfc2074_unquote('=?utf-8?b?%s?=' % bad_b64, strict=True),
            bad_b64)

    def test_strict_decoding_failures(self):
        for bad in (
            '=?utf-8?Q?=AF=E4=BB=B6?=',
            '=?utf-8?Q?=BA=A6=E7=9A=84=E9=82=AE=E4=BB=B6=E7=BE=A4=E5=8F=91=E8=BD?=',
            '=?GB2312?B?v6q2kMaxbDM2Mk85MTIzN2w=?=',
 
        ):
            rv = rfc2074_unquote(bad, strict=True)
            self.assertEquals(rv, bad.split('?')[3])

    def test_test_cases_from_email(self):
        for c, (i, s, o) in enumerate((

            # Weird mixed utf-8 data in quoted-printable string
            ('=?iso-8859-1?Q?Listnámskei=F0=20fyrir=20börn=20?=',
                True, 'Listnámskeið fyrir börn '),

            # Similar to above, but recover from invalid encoding
            ('=?utf-8?Q?Listnámskei=C3=B0=20fyrir=20börn=20?=',
                False, 'Listnámskeið fyrir börn '),

                )):
            try:
                rv = '(Failed)'
                if o is not None:
                    rv = rfc2074_unquote(i, strict=s)
                    self.assertEquals(rv, o)
                else:
                    try:
                        rv = rfc2074_unquote(i)
                        assert(not 'reached')
                    except:
                        pass
            except AssertionError:
                self.assertEquals(rv, 0)
