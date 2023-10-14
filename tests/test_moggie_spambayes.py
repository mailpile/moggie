import unittest

import moggie.util.spambayes


class SpambayesTests(unittest.TestCase):
    def test_classifier(self):
        sb = moggie.util.spambayes.Classifier()

        sb.learn('hello world this is great'.split(), False)
        sb.learn('I like spam and ham is good too'.split(), True)

        self.assertGreater(sb.classify('This is greaet spam I like'.split()), 0.5)
        self.assertGreater(sb.classify('I like the world of spam'.split()), 0.5)
        self.assertLess(sb.classify('Hello world this is ham'.split()), 0.5)
        self.assertLess(sb.classify('This is a great world'.split()), 0.5)

        dump = dict(sb)
        self.assertEquals(dump['*'], (1, 1))
        self.assertEquals(dump['hello'], (0, 1))
        self.assertEquals(dump['world'], (0, 1))
        self.assertEquals(dump['spam'], (1, 0))
        self.assertEquals(dump['ham'], (1, 0))
        self.assertEquals(dump['is'], (1, 1))

        sb2 = moggie.util.spambayes.Classifier().load(dump.items())
        self.assertGreater(sb2.classify('This is greaet spam I like'.split()), 0.5)
        self.assertGreater(sb2.classify('I like the world of spam'.split()), 0.5)
        self.assertLess(sb2.classify('Hello world this is ham'.split()), 0.5)
        self.assertLess(sb2.classify('This is a great world'.split()), 0.5)
