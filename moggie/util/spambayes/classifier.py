#! /usr/bin/env python
#
# This is code from Spambayes 1.1b3, simplified for use within moggie.
# The original LICENSE.TXT file is included in the same subdirectory.
#
##############################################################################
#
# An implementation of a Bayes-like spam classifier.
#
# Paul Graham's original description:
#
#     http://www.paulgraham.com/spam.html
#
# A highly fiddled version of that can be retrieved from our CVS repository,
# via tag Last-Graham.  This made many demonstrated improvements in error
# rates over Paul's original description.
#
# This code implements Gary Robinson's suggestions, the core of which are
# well explained on his webpage:
#
#    http://radio.weblogs.com/0101454/stories/2002/09/16/spamDetection.html
#
# This is theoretically cleaner, and in testing has performed at least as
# well as our highly tuned Graham scheme did, often slightly better, and
# sometimes much better.  It also has "a middle ground", which people like:
# the scores under Paul's scheme were almost always very near 0 or very near
# 1, whether or not the classification was correct.  The false positives
# and false negatives under Gary's basic scheme (use_gary_combining) generally
# score in a narrow range around the corpus's best spam_cutoff value.
# However, it doesn't appear possible to guess the best spam_cutoff value in
# advance, and it's touchy.
#
# The last version of the Gary-combining scheme can be retrieved from our
# CVS repository via tag Last-Gary.
#
# The chi-combining scheme used by default here gets closer to the theoretical
# basis of Gary's combining scheme, and does give extreme scores, but also
# has a very useful middle ground (small # of msgs spread across a large range
# of scores, and good cutoff values aren't touchy).
#
# This implementation is due to Tim Peters et alia.

import math

from .chi2 import chi2Q


LN2 = math.log(2)       # used frequently by chi-combining


class WordInfo(object):
    # A WordInfo is created for each distinct word.  spamcount is the
    # number of trained spam msgs in which the word appears, and hamcount
    # the number of trained ham msgs.
    #
    # Invariant:  For use in a classifier database, at least one of
    # spamcount and hamcount must be non-zero.
    #
    # Important:  This is a tiny object.  Use of __slots__ is essential
    # to conserve memory.
    __slots__ = 'spamcount', 'hamcount'

    def __init__(self, spamcount=0, hamcount=0):
        self.spamcount, self.hamcount = (spamcount, hamcount)

    def __repr__(self):
        return "WordInfo" + repr((self.spamcount, self.hamcount))


class Classifier:
    """
    An implementation of a Bayes-like spam classifier. Originally from the
    spambayes project, simplified and adapted for use with moggie.

    This implementation is due to Tim Peters et alia.

    Classifier options and their defaults are described below.

    use_chi_squared_combining=True

      For vectors of random, uniformly distributed probabilities,
      -2*sum(ln(p_i)) follows the chi-squared distribution with 2*n degrees
      of freedom.  This is the "provably most-sensitive" test the original
      scheme was monotonic with.  Getting closer to the theoretical basis
      appears to give an excellent combining method, usually very extreme in
      its judgment, yet finding a tiny (in # of msgs, spread across a huge
      range of scores) middle ground where lots of the mistakes live.  This
      is the best method so far. One systematic benefit is is immunity to
      "cancellation disease". One systematic drawback is sensitivity to
      *any* deviation from a uniform distribution, regardless of whether
      actually evidence of ham or spam. Rob Hooft alleviated that by
      combining the final S and H measures via (S-H+1)/2 instead of via
      S/(S+H)). In practice, it appears that setting ham_cutoff=0.05, and
      spam_cutoff=0.95, does well across test sets; while these cutoffs are
      rarely optimal, they get close to optimal.  With more training data,
      Tim has had good luck with ham_cutoff=0.30 and spam_cutoff=0.80 across
      three test data sets (original c.l.p data, his own email, and newer
      general python.org traffic).

    use_bigrams=False

      Generate both unigrams (words) and bigrams (pairs of
      words). However, extending an idea originally from Gary Robinson,
      the message is 'tiled' into non-overlapping unigrams and bigrams,
      approximating the strongest outcome over all possible tilings.

      Note that to really test this option you need to retrain with it on,
      so that your database includes the bigrams - if you subsequently turn
      it off, these tokens will have no effect.  This option will at least
      double your database size given the same training data, and will
      probably at least triple it.

      You may also wish to increase the max_discriminators (maximum number
      of extreme words) option if you enable this option, perhaps doubling or
      quadrupling it.  It's not yet clear.  Bigrams create many more hapaxes,
      and that seems to increase the brittleness of minimalist training
      regimes; increasing max_discriminators may help to soften that effect.
      OTOH, max_discriminators defaults to 150 in part because that makes it
      easy to prove that the chi-squared math is immune from numeric
      problems.  Increase it too much, and insane results will eventually
      result (including fatal floating-point exceptions on some boxes).

      This option is experimental, and may be removed in a future release.
      We would appreciate feedback about it if you use it - email
      spambayes@python.org with your comments and results.

    unknown_word_prob=0.5

      These two control the prior assumption about word probabilities.
      unknown_word_prob is essentially the probability given to a word that
      has never been seen before.  Nobody has reported an improvement via
      moving it away from 1/2, although Tim has measured a mean spamprob of
      a bit over 0.5 (0.51-0.55) in 3 well-trained classifiers.

    unknown_word_strength=0.45

      This adjusts how much weight to give the prior assumption relative to
      the probabilities estimated by counting.  At 0 the counting estimates
      are believed 100%, even to the extent of assigning certainty (0 or 1)
      to a word that has appeared in only ham or only spam. This is a disaster.

      As unknown_word_strength tends toward infinity, all probabilities
      tend toward unknown_word_prob.  All reports were that a value near 0.4
      worked best, so this does not seem to be corpus-dependent.

    minimum_prob_strength=0.1

      When scoring a message, ignore all words with
      abs(word.spamprob - 0.5) < minimum_prob_strength. This may be a hack,
      but it has proved to reduce error rates in many tests.
      0.1 appeared to work well across all corpora.

    max_discriminators=150

      The maximum number of extreme words to look at in a message, where
      "extreme" means with spam probability farthest away from 0.5. 150
      appears to work well across all corpora tested.

    """
    # allow a subclass to use a different class for WordInfo
    WordInfoClass = WordInfo

    def __init__(self,
            use_chi_squared_combining=True,
            use_bigrams=False,
            unknown_word_prob=0.5,
            unknown_word_strength=0.45,
            minimum_prob_strength=0.1,
            max_discriminators=150):

        self.use_chi_squared_combining = use_chi_squared_combining
        self.use_bigrams = use_bigrams
        self.unknown_word_prob = unknown_word_prob
        self.unknown_word_strength = unknown_word_strength
        self.minimum_prob_strength = minimum_prob_strength
        self.max_discriminators = max_discriminators

        self.wordinfo = {}
        self.probcache = {}
        self.nspam = self.nham = 0

        if self.use_chi_squared_combining:
            self.spamprob = self.chi2_spamprob
        self.classify = self.spamprob

    def __iter__(self):
        yield '*', self.nspam, self.nham
        for word, info in self.wordinfo.items():
            yield word, info.spamcount, info.hamcount

    def decay(self, ratio):
        dropping = []
        scale = 1.0 - ratio
        for word, info in self.wordinfo.items():
            info.spamcount = scale * info.spamcount
            info.hamcount = scale * info.hamcount
            if (info.spamcount < 0.5) and (info.hamcount < 0.5):
                dropping.append(word)
        self.nspam = int(scale * self.nspam)
        self.nham = int(scale * self.nham)
        for word in dropping:
            del self.wordinfo[word]
        return len(dropping)

    def load(self, iterator):
        self.wordinfo = {}
        self.probcache = {}
        for word, spamcount, hamcount in iterator:
            self.wordinfo[word] = self.WordInfoClass(spamcount, hamcount)
        totals = self.wordinfo.pop('*')
        self.nspam = totals.spamcount
        self.nham = totals.hamcount
        return self

    # spamprob() implementations.  One of the following is aliased to
    # spamprob, depending on option settings.
    # Currently only chi-squared is available, but maybe there will be
    # an alternative again someday.

    # Across vectors of length n, containing random uniformly-distributed
    # probabilities, -2*sum(ln(p_i)) follows the chi-squared distribution
    # with 2*n degrees of freedom.  This has been proven (in some
    # appropriate sense) to be the most sensitive possible test for
    # rejecting the hypothesis that a vector of probabilities is uniformly
    # distributed.  Gary Robinson's original scheme was monotonic *with*
    # this test, but skipped the details.  Turns out that getting closer
    # to the theoretical roots gives a much sharper classification, with
    # a very small (in # of msgs), but also very broad (in range of scores),
    # "middle ground", where most of the mistakes live.  In particular,
    # this scheme seems immune to all forms of "cancellation disease":  if
    # there are many strong ham *and* spam clues, this reliably scores
    # close to 0.5.  Most other schemes are extremely certain then -- and
    # often wrong.
    def chi2_spamprob(self, wordstream, evidence=False):
        """Return best-guess probability that wordstream is spam.

        wordstream is an iterable object producing words.
        The return value is a float in [0.0, 1.0].

        If optional arg evidence is True, the return value is a pair
            probability, evidence
        where evidence is a list of (word, probability) pairs.
        """

        from math import frexp, log as ln

        # We compute two chi-squared statistics, one for ham and one for
        # spam.  The sum-of-the-logs business is more sensitive to probs
        # near 0 than to probs near 1, so the spam measure uses 1-p (so
        # that high-spamprob words have greatest effect), and the ham
        # measure uses p directly (so that lo-spamprob words have greatest
        # effect).
        #
        # For optimization, sum-of-logs == log-of-product, and f.p.
        # multiplication is a lot cheaper than calling ln().  It's easy
        # to underflow to 0.0, though, so we simulate unbounded dynamic
        # range via frexp.  The real product H = this H * 2**Hexp, and
        # likewise the real product S = this S * 2**Sexp.
        H = S = 1.0
        Hexp = Sexp = 0

        clues = self._getclues(wordstream)
        for prob, word, record in clues:
            prob = max(1e-10, min(prob, 1 - 1e-10))  # Bound our probabilities
            S *= 1.0 - prob
            H *= prob
            if S < 1e-200:  # prevent underflow
                S, e = frexp(S)
                Sexp += e
            if H < 1e-200:  # prevent underflow
                H, e = frexp(H)
                Hexp += e

        # Compute the natural log of the product = sum of the logs:
        # ln(x * 2**i) = ln(x) + i * ln(2).
        S = ln(S) + Sexp * LN2
        H = ln(H) + Hexp * LN2

        n = len(clues)
        if n:
            S = 1.0 - chi2Q(-2.0 * S, 2*n)
            H = 1.0 - chi2Q(-2.0 * H, 2*n)

            # How to combine these into a single spam score?  We originally
            # used (S-H)/(S+H) scaled into [0., 1.], which equals S/(S+H).  A
            # systematic problem is that we could end up being near-certain
            # a thing was (for example) spam, even if S was small, provided
            # that H was much smaller.
            # Rob Hooft stared at these problems and invented the measure
            # we use now, the simpler S-H, scaled into [0., 1.].
            prob = (S-H + 1.0) / 2.0
        else:
            prob = 0.5

        if evidence:
            clues = [(w, p) for p, w, _r in clues]
            clues.sort(key=lambda a: a[1])
            clues.insert(0, ('*S*', S))
            clues.insert(0, ('*H*', H))
            return prob, clues
        else:
            return prob

    def learn(self, wordstream, is_spam):
        """Teach the classifier by example.

        wordstream is a word stream representing a message.  If is_spam is
        True, you're telling the classifier this message is definitely spam,
        else that it's definitely not spam.
        """
        if self.use_bigrams:
            wordstream = self._enhance_wordstream(wordstream)
        self._add_msg(wordstream, is_spam)

    def unlearn(self, wordstream, is_spam):
        """In case of pilot error, call unlearn ASAP after screwing up.

        Pass the same arguments you passed to learn().
        """
        if self.use_bigrams:
            wordstream = self._enhance_wordstream(wordstream)
        self._remove_msg(wordstream, is_spam)

    def probability(self, record):
        """Compute, store, and return prob(msg is spam | msg contains word).

        This is the Graham calculation, but stripped of biases, and
        stripped of clamping into 0.01 thru 0.99.  The Bayesian
        adjustment following keeps them in a sane range, and one
        that naturally grows the more evidence there is to back up
        a probability.
        """

        nham = float(self.nham or 1)
        nspam = float(self.nspam or 1)

        # We may occasionally end up with spamcount > nspam, because of
        # rounding errors in decay(). Taking the minimums compensates.
        spamcount = min(nspam, record.spamcount)
        hamcount = min(nham, record.hamcount)

        # Try the cache first
        try:
            return self.probcache[spamcount][hamcount]
        except KeyError:
            pass

        hamratio = hamcount / nham
        spamratio = spamcount / nspam

        prob = spamratio / (hamratio + spamratio)

        S = self.unknown_word_strength
        StimesX = S * self.unknown_word_prob


        # Now do Robinson's Bayesian adjustment.
        #
        #         s*x + n*p(w)
        # f(w) = --------------
        #           s + n
        #
        # I find this easier to reason about like so (equivalent when
        # s != 0):
        #
        #        x - p
        #  p +  -------
        #       1 + n/s
        #
        # IOW, it moves p a fraction of the distance from p to x, and
        # less so the larger n is, or the smaller s is.

        n = hamcount + spamcount
        prob = (StimesX + n * prob) / (S + n)

        # Update the cache
        try:
            self.probcache[spamcount][hamcount] = prob
        except KeyError:
            self.probcache[spamcount] = {hamcount: prob}

        return prob

    # NOTE:  Graham's scheme had a strange asymmetry:  when a word appeared
    # n>1 times in a single message, training added n to the word's hamcount
    # or spamcount, but predicting scored words only once.  Tests showed
    # that adding only 1 in training, or scoring more than once when
    # predicting, hurt under the Graham scheme.
    # This isn't so under Robinson's scheme, though:  results improve
    # if training also counts a word only once.  The mean ham score decreases
    # significantly and consistently, ham score variance decreases likewise,
    # mean spam score decreases (but less than mean ham score, so the spread
    # increases), and spam score variance increases.
    # I (Tim) speculate that adding n times under the Graham scheme helped
    # because it acted against the various ham biases, giving frequently
    # repeated spam words (like "Viagra") a quick ramp-up in spamprob; else,
    # adding only once in training, a word like that was simply ignored until
    # it appeared in 5 distinct training spams.  Without the ham-favoring
    # biases, though, and never ignoring words, counting n times introduces
    # a subtle and unhelpful bias.
    # There does appear to be some useful info in how many times a word
    # appears in a msg, but distorting spamprob doesn't appear a correct way
    # to exploit it.
    def _add_msg(self, wordstream, is_spam):
        self.probcache = {}    # nuke the prob cache
        if is_spam:
            self.nspam += 1
        else:
            self.nham += 1

        for word in set(wordstream):
            record = self._wordinfoget(word)
            if record is None:
                record = self.WordInfoClass()

            if is_spam:
                record.spamcount += 1
            else:
                record.hamcount += 1

            self._wordinfoset(word, record)

        self._post_training()

    def _remove_msg(self, wordstream, is_spam):
        self.probcache = {}    # nuke the prob cache
        if is_spam:
            if self.nspam <= 0:
                raise ValueError("spam count would go negative!")
            self.nspam -= 1
        else:
            if self.nham <= 0:
                raise ValueError("non-spam count would go negative!")
            self.nham -= 1

        for word in set(wordstream):
            record = self._wordinfoget(word)
            if record is not None:
                if is_spam:
                    if record.spamcount > 0:
                        record.spamcount -= 1
                else:
                    if record.hamcount > 0:
                        record.hamcount -= 1
                if record.hamcount == 0 == record.spamcount:
                    self._wordinfodel(word)
                else:
                    self._wordinfoset(word, record)

        self._post_training()

    def _post_training(self):
        """This is called after training on a wordstream.  Subclasses might
        want to ensure that their databases are in a consistent state at
        this point.  Introduced to fix bug #797890."""
        pass

    # Return list of (prob, word, record) triples, sorted by increasing
    # prob.  "word" is a token from wordstream; "prob" is its spamprob (a
    # float in 0.0 through 1.0); and "record" is word's associated
    # WordInfo record if word is in the training database, or None if it's
    # not.  No more than max_discriminators items are returned, and have
    # the strongest (farthest from 0.5) spamprobs of all tokens in wordstream.
    # Tokens with spamprobs less than minimum_prob_strength away from 0.5
    # aren't returned.
    def _getclues(self, wordstream):
        mindist = self.minimum_prob_strength

        if self.use_bigrams:
            # This scheme mixes single tokens with pairs of adjacent tokens.
            # wordstream is "tiled" into non-overlapping unigrams and
            # bigrams.  Non-overlap is important to prevent a single original
            # token from contributing to more than one spamprob returned
            # (systematic correlation probably isn't a good thing).

            # First fill list raw with
            #     (distance, prob, word, record), indices
            # pairs, one for each unigram and bigram in wordstream.
            # indices is a tuple containing the indices (0-based relative to
            # the start of wordstream) of the tokens that went into word.
            # indices is a 1-tuple for an original token, and a 2-tuple for
            # a synthesized bigram token.  The indices are needed to detect
            # overlap later.
            raw = []
            push = raw.append
            pair = None
            # Keep track of which tokens we've already seen.
            # Don't use a set here!  This is an innermost loop, so speed is
            # important here (direct dict fiddling is much quicker than
            # invoking Python-level set methods; in Python 2.4 that will
            # change).
            seen = {pair: 1} # so the bigram token is skipped on 1st loop trip
            for i, token in enumerate(wordstream):
                if i:   # not the 1st loop trip, so there is a preceding token
                    # This string interpolation must match the one in
                    # _enhance_wordstream().
                    pair = "bi:%s %s" % (last_token, token)
                last_token = token
                for clue, indices in (token, (i,)), (pair, (i-1, i)):
                    if clue not in seen:    # as always, skip duplicates
                        seen[clue] = 1
                        tup = self._worddistanceget(clue)
                        if tup[0] >= mindist:
                            push((tup, indices))

            # Sort raw, strongest to weakest spamprob.
            raw.sort()
            raw.reverse()
            # Fill clues with the strongest non-overlapping clues.
            clues = []
            push = clues.append
            # Keep track of which indices have already contributed to a
            # clue in clues.
            seen = {}
            for tup, indices in raw:
                overlap = [i for i in indices if i in seen]
                if not overlap: # no overlap with anything already in clues
                    for i in indices:
                        seen[i] = 1
                    push(tup)
            # Leave sorted from smallest to largest spamprob.
            clues.reverse()

        else:
            # The all-unigram scheme just scores the tokens as-is.  A set()
            # is used to weed out duplicates at high speed.
            clues = []
            push = clues.append
            for word in set(wordstream):
                tup = self._worddistanceget(word)
                if tup[0] >= mindist:
                    push(tup)
            clues.sort()

        if len(clues) > self.max_discriminators:
            del clues[0 : -self.max_discriminators]
        # Return (prob, word, record).
        return [t[1:] for t in clues]

    def _worddistanceget(self, word):
        record = self._wordinfoget(word)
        if record is None:
            prob = self.unknown_word_prob
        else:
            prob = self.probability(record)
        distance = abs(prob - 0.5)
        return distance, prob, word, record

    def _wordinfoget(self, word):
        return self.wordinfo.get(word)

    def _wordinfoset(self, word, record):
        self.wordinfo[word] = record

    def _wordinfodel(self, word):
        del self.wordinfo[word]

    def _enhance_wordstream(self, wordstream):
        """Add bigrams to the wordstream.

        For example, a b c -> a b "a b" c "b c"

        Note that these are *token* bigrams, and not *word* bigrams - i.e.
        'synthetic' tokens get bigram'ed, too.

        The bigram token is simply "bi:unigram1 unigram2" - a space should
        be sufficient as a separator, since spaces aren't in any other
        tokens, apart from 'synthetic' ones.  The "bi:" prefix is added
        to avoid conflict with tokens we generate (like "subject: word",
        which could be "word" in a subject, or a bigram of "subject:" and
        "word").

        If the "Classifier":"use_bigrams" option is removed, this function
        can be removed, too.
        """

        last = None
        for token in wordstream:
            yield token
            if last:
                # This string interpolation must match the one in
                # _getclues().
                yield "bi:%s %s" % (last, token)
            last = token

    def _wordinfokeys(self):
        return self.wordinfo.keys()
