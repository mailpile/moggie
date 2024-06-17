import unittest
import doctest

import moggie.security.filenames


class DoctestTests(unittest.TestCase):
    def run_doctests(self, module):
        results = doctest.testmod(module)
        if results.failed:
            print(results)
        self.assertFalse(results.failed)

    def test_doctests_filenames(self):
        self.run_doctests(moggie.security.filenames)
