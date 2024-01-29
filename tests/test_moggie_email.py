import unittest
import doctest

import moggie.email.addresses
import moggie.email.sync


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


class SyncTests(unittest.TestCase):
    def test_sync_parse(self):
        self.assertTrue(True)

