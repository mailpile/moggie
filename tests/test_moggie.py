import asyncio
import os
import shlex
import unittest

from moggie import Moggie


class BasicMoggieTest(unittest.TestCase):
    def setUpClass():
        BasicMoggieTest.work_dir = os.path.join(
            os.path.dirname(__file__), '..', 'tmp', 'moggie-test')
        if not os.path.exists(BasicMoggieTest.work_dir):
            os.mkdir(BasicMoggieTest.work_dir, 0o0755)
        BasicMoggieTest.moggie = Moggie(work_dir=BasicMoggieTest.work_dir)
        BasicMoggieTest.moggie.start()

    def tearDownClass():
        BasicMoggieTest.moggie.stop()
        os.system(shlex.join(['rm', '-rf', BasicMoggieTest.work_dir]))

    def test_moggie_help(self):
        _help1 = self.moggie.help()[0]
        self.assertTrue('moggie search' in _help1)

    def test_moggie_help2(self):
        self.assertEqual(1, 1)
