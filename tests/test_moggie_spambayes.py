import unittest

import moggie.util.spambayes
import moggie.search.filters


class SpambayesTests(unittest.TestCase):
    def test_classifier(self):
        sb = moggie.util.spambayes.Classifier()

        sb.learn('hello world this is great'.split(), False)
        sb.learn('I like spam and ham is good too'.split(), True)

        self.assertLess(0.5, sb.classify('This is great spam I like'.split()))
        self.assertLess(0.5, sb.classify('I like the world of spam'.split()))
        self.assertLess(sb.classify('Hello world this is ham'.split()), 0.5)
        self.assertLess(sb.classify('This is a great world'.split()), 0.5)

        dump_list = list(sb)
        dump = dict((k, (sc, hc)) for k, sc, hc in dump_list)
        self.assertEquals(dump['*'], (1, 1))
        self.assertEquals(dump['hello'], (0, 1))
        self.assertEquals(dump['world'], (0, 1))
        self.assertEquals(dump['spam'], (1, 0))
        self.assertEquals(dump['ham'], (1, 0))
        self.assertEquals(dump['is'], (1, 1))

        sb2 = moggie.util.spambayes.Classifier().load(dump_list)
        self.assertLess(0.5, sb2.classify('This is great spam I like'.split()))
        self.assertLess(0.5, sb2.classify('I like the world of spam'.split()))
        self.assertLess(sb2.classify('Hello world this is ham'.split()), 0.5)
        self.assertLess(sb2.classify('This is a great world'.split()), 0.5)


class AutoTaggerTests(unittest.TestCase):
    TEST_JSON = """\
        {
            "tag": "spam",
            "spam_ids": [1],
            "ham_ids": [2],
            "min_trained": 0,
            "threshold": 0.9,
            "training_auto": true,
            "trained_version": 0,
            "classifier": "spambayes",
            "data": [
                ["*",     1, 1],
                ["hello", 0, 1],
                ["world", 0, 1],
                ["this",  0, 1],
                ["is",    1, 1],
                ["great", 0, 1],
                ["I",     1, 0],
                ["like",  1, 0],
                ["spam",  1, 0],
                ["and",   1, 0],
                ["ham",   1, 0],
                ["good",  1, 0],
                ["too",   1, 0]
            ]
        }"""

    def test_autotagger(self):
        class TestAutoTagger(moggie.search.filters.AutoTagger):
            pass
        at = TestAutoTagger(salt=None).from_json(self.TEST_JSON)
        self.assertEquals(at.tag, 'spam')
        self.assertEquals(at.spam_ids, [1])
        self.assertEquals(at.ham_ids, [2])
        self.assertEquals(at.classifier_type, 'spambayes')
        self.assertEquals(at.info, {})
        self.assertLess(0.5, at.classify('this is great spam I like'.split()))
        self.assertLess(0.5, at.classify('I like the world of spam'.split()))
        self.assertLess(at.classify('hello world this is ham'.split()), 0.5)
        self.assertLess(at.classify('this is a great world'.split()), 0.5)

        # Test JSON generation
        self.maxDiff = None
        our_json = self.TEST_JSON.replace(' ', '').replace('\n', '')
        self.assertEquals(our_json, at.to_json())

        # Make sure that the min_trained threshold is respected
        rt = TestAutoTagger(salt=None).from_json(self.TEST_JSON)
        rt.min_trained = 250
        self.assertEquals(rt.classify('this is great spam I like'.split()), 0.5)
        self.assertEquals(rt.classify('hello world this is ham'.split()), 0.5)

    def test_autotagger_evidence(self):
        at = moggie.search.filters.AutoTagger(salt='Testing')
        at.min_trained = 0

        at.learn(1, 'hello world this is great'.split(), is_spam=False)
        at.learn(2, 'I like spam and ham is good too'.split(), is_spam=True)

        # Test the evidence function...
        rank, clues =  at.classify('this is great spam'.split(), evidence=True)
        clues = dict(clues)
        self.assertLess(rank, 0.5)
        self.assertLess(clues['this'], 0.5)
        self.assertLess(clues['great'], 0.5)
        self.assertLess(0.5, clues['spam'])
