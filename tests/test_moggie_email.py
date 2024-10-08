import unittest
import doctest

import moggie.email.addresses
import moggie.email.headers
import moggie.email.parsemime
import moggie.email.sync
import moggie.email.util


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

