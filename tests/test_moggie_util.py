import unittest
import doctest

import moggie.util.imap
#import moggie.util.conn_brokers
import moggie.util.mailpile

import moggie.util.dumbcode
import moggie.util.friendly


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

