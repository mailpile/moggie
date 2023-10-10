import asyncio
import os
import shlex
import unittest

from moggie import Moggie


class OfflineMoggieTest(unittest.TestCase):
    def test_moggie_help(self):
        self.assertRegex(Moggie().help()[0]['text'], 'moggie search')

    def test_moggie_email(self):
        moggie = Moggie()
        email1 = moggie.email(
            _from='bre@example.org',
            to='bjarni@example.org',
            html='N',
            message='Hello world, this is great')[0]

        self.assertRegex(email1, b'Content-Type: text/plain;')
        self.assertRegex(email1, b'To: <bjarni@example.org>')
        self.assertRegex(email1, b'From: <bre@example.org>')
        self.assertRegex(email1, b'Hello world, this is great')

    def test_moggie_parse(self):
        moggie = Moggie()
        hello = 'Hello world, this is great'
        email1 = moggie.email(
            _from='bre@example.org',
            to='bjarni@example.org',
            html='N',
            message=hello)[0]

        parse1 = moggie.parse(stdin=email1)[0]['parsed']

        self.assertEqual(parse1['_ORDER'], [
            'mime-version', 'content-type', 'content-disposition',
            'content-transfer-encoding', 'message-id', 'date', 'from', 'to'])
        self.assertEqual(parse1['_PARTS'][0]['_TEXT'].strip(), hello)


class OnlineMoggieTest(unittest.TestCase):
    def setUpClass():
        OnlineMoggieTest.work_dir = wd = os.path.join(
            os.path.dirname(__file__), '..', 'tmp', 'moggie-test')

        os.system(shlex.join(['rm', '-rf', wd]))
        os.mkdir(wd, 0o0755)
        moggie = OnlineMoggieTest.moggie = Moggie(work_dir=wd)
        moggie.set_access(True)
        moggie.start()

    def tearDownClass():
        OnlineMoggieTest.moggie.stop()
        #os.system(shlex.join(['rm', '-rf', OnlineMoggieTest.work_dir]))

    def test_moggie_help(self):
        self.assertRegex(self.moggie.help()[0]['text'], 'moggie search')

    def test_moggie_count(self):
        self.assertTrue('*' in self.moggie.count()[0])

    def test_moggie_help2(self):
        self.assertEqual(1, 1)
