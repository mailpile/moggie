import asyncio
import os
import shlex
import time
import unittest

from moggie import Moggie


class OfflineMoggieTest(unittest.TestCase):
    """
    Tests for moggie commands which should run without a live back-end.
    """
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
    """
    Tests for moggie commands which require a working, live backend.

    Ultimately, every major moggie API command *should* be tested here to
    ensure our API stays unbroken as development proceeds!
    """
    def setUpClass():
        OnlineMoggieTest.work_dir = wd = os.path.join(
            os.path.dirname(__file__), '..', 'tmp', 'moggie-test')
        OnlineMoggieTest.test_email_dir = os.path.join(
            os.path.dirname(__file__), '..', 'test-data', 'emails')

        os.system(shlex.join(['rm', '-rf', wd]))
        os.mkdir(wd, 0o0755)
        moggie = OnlineMoggieTest.moggie = Moggie(work_dir=wd)
        moggie.set_access(True)
        moggie.start()

    def tearDownClass():
        OnlineMoggieTest.moggie.stop()
        os.system(shlex.join(['rm', '-rf', OnlineMoggieTest.work_dir]))

    def test_moggie_001_help(self):
        self.assertRegex(self.moggie.help()[0]['text'], 'moggie search')

    def test_moggie_002_count(self):
        self.assertTrue('*' in self.moggie.count()[0])

    def test_moggie_003_import_new(self):
        self.moggie.import_(self.test_email_dir, tag='inbox', config_only=True)
        self.moggie.new()

        terms = ['all:mail', 'in:incoming', 'in:inbox']
        for tries in range(0, 100):
            counts = self.moggie.count(*terms, multi=True)[0]
            if counts['all:mail'] > 5 and counts['in:incoming'] == 0:
                break
            time.sleep(0.100)
        self.assertTrue(counts['in:incoming'] == 0)
        self.assertTrue(counts['in:inbox'] > 5)
        self.assertTrue(counts['all:mail'] > 5)

    def test_moggie_004_search(self):
        self.assertFalse(self.moggie.search('in:incoming'))  # No results

        results = self.moggie.search('alice', 'subject:autocrypt')
        self.assertEquals(len(results), 1)
        self.assertEquals(results[0]['date_relative'], '2023-06-20')
        self.assertEquals(results[0]['authors'],       'Alice Lövelace')
        self.assertEquals(results[0]['tags'],          ['inbox'])

        # FIXME: Many, many more tests! Search has so many different modes
