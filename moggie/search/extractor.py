import copy
import re
import time

import email.utils

from ..util.mailpile import msg_id_hash
from ..security.html import HTMLCleaner
from .headerprint import HeaderPrints
from .dates import ts_to_keywords


WORD_REGEXP = re.compile('[\w’\']{2,}')
WORD_STRIP = re.compile(r'[’\']+')
MIXED_REGEXP = re.compile('^([a-zA-Z]+\d|\d+[a-zA-Z])')

DOMAIN_REGEXP = re.compile(r'[a-zA-Z0-9\._-]+(?:\.[a-zA-Z0-9\._-]+)*')
URL_REGEXP = re.compile(
    r'((https?://|mailto:|www\.|[a-zA-Z0-9\._-]+@)[a-zA-Z0-9\._-]+[^\s)>]*)')

STOPLIST = set([
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'an', 'and',
    'any', 'are', 'as', 'at', 'but', 'by', 'can', 'div', 'do', 'for',
    'from', 'has', 'hello', 'hi', 'i', 'in', 'if', 'is', 'it', 'mailto',
    'me', 'my', 'og', 'of', 'on', 'or', 'p', 're', 'span', 'so', 'that',
    'the', 'this', 'td', 'to', 'tr', 'was', 'we', 'were', 'you'])

BORING_HEADERS = set([
    'received', 'received-spf', 'date', 'autocrypt', 'content-type',
    'content-disposition', 'mime-version', 'list-archive', 'list-help',
    'list-unsubscribe', 'dkim-signature', 'domainkey-signature',
    'arc-message-signature', 'arc-seal', 'arc-authentication-results',
    'authentication-results'])

EXPECTED_HEADERS = set([
    'from', 'to', 'subject', 'date', 'message-id'])


class KeywordExtractor:
    def __init__(self,
            stoplist=STOPLIST,
            min_word_length=2,
            max_word_length=45):

        self.stoplist = copy.copy(stoplist)
        self.min_word_length = min_word_length
        self.max_word_length = max_word_length

        # FIXME: 
        #   - Make this configurable, somehow
        #   - Plugins?
        #   - Language/locale specific rules?
        pass

    def url_domains(self, txt):
        # FIXME: Also returns e-mail addresses... hrm.
        def _domain(url):
            if '://' in url:
                url = url.split('/')[2]
            elif ':' in url:
                url = url.replace('?', ':').split(':')[1]
            else:
                url = url.split('/')[0]

            if '@' in url:
                return url
            m = DOMAIN_REGEXP.findall(url)
            if m:
                return m[0]

            return None

        return [_domain(m[0]) for m in URL_REGEXP.findall(txt)]

    def words(self, txt, strip_urls=True, url_domains=None):
        """
        Extract keywords from a block of text. URLs and e-mail addresses
        are recognized and the full domains and addresses are returned as
        individual keywords in addition to the fragments within them; URL
        paths and query-string arguments are ignored.
        """
        url_domains = set(u for u in url_domains if u) if url_domains else set()

        if strip_urls:
            txt = WORD_STRIP.sub('', URL_REGEXP.sub(' ', txt))
        else:
            txt = WORD_STRIP.sub('', txt)

        if url_domains:
            txt += '\n' + '\n'.join(url_domains)

        def _keep(w):
            if ((len(w) > self.max_word_length) or
                    (len(w) < 7 and MIXED_REGEXP.match(w))):
                return False
            return True

        ltxt = txt.lower()
        wordlist = [w for w in WORD_REGEXP.findall(ltxt) if _keep(w)]
        words = set(w for w in wordlist if self.min_word_length <= len(w))

        for i in range(0, len(wordlist) - 1):
            if (len(wordlist[i]) <= 3) or (len(wordlist[i+1]) <= 3):
                combined = '%s %s' % (wordlist[i], wordlist[i+1])
                if len(combined) <= self.max_word_length:
                    words.add(combined)

        return (url_domains | words) - self.stoplist

    def _parse_html(self, text):
        words = []
        def _collect(tag, attrs, data):
            if tag not in ('script', 'style'):
                words.append(data.strip())

        # FIXME: Wait, we should also be extracting URLs in a more
        #        structured way. DUH.

        hc = HTMLCleaner(text, callbacks={'DATA': _collect})
        hc.close()
        return ' '.join(words), hc.keywords

    def body_text_keywords(self, parsed_email):
        status, keywords = set(), set()
        text_chars = 0
        url_count = 0
        for part in parsed_email.get('_PARTS') or []:
            text = part.get('_TEXT')
            if text:
                if part.get('content-type', [None])[0] == 'text/html':
                    text, html_kw = self._parse_html(text)
                    keywords.add('has:html')
                    keywords |= html_kw
                else:
                    keywords.add('has:text')
                ud = self.url_domains(text)
                keywords |= self.words(text, url_domains=ud)
                text_chars += len(text)
                url_count += len(ud)
            elif 'attachment' in part.get('content-disposition', []):
                keywords.add('has:attachment')

        if url_count:
            keywords.add('has:urls')
            if url_count > 10:
                keywords.add('has:many_urls')

        if text_chars < 50*6:
            keywords.add('is:short')
        elif text_chars > 200*6:
            keywords.add('is:long')

        return status, keywords

    def header_keywords(self, metadata, parsed_email):
        status, keywords = set(), set()

        if metadata and metadata.timestamp:
            keywords |= set(ts_to_keywords(metadata.timestamp))
        elif parsed_email.get('date'):
            ts = int(time.mktime(email.utils.parsedate(parsed_email['date'])))
            if ts > 0:
                keywords |= set(ts_to_keywords(ts))

        # Record the same message-ID-hashes as Mailpile v1 did
        keywords.add('msgid:' + msg_id_hash(parsed_email.get('message-id')))

        subject = parsed_email.get('subject')
        if subject:
            ud = self.url_domains(subject)
            sk = self.words(subject, url_domains=ud)
            keywords |= sk
            keywords |= set('subject:%s' % kw for kw in sk)
        else:
            keywords.add('no:subject')

        for kw, hdr in (
                ('from', 'from'),
                ('from', 'sender'),
                ('from', 'reply-to'),
                ('from', 'resent-from'),
                ('from', 'x-original-from'),
                ('to',   'to'),
                ('to',   'cc'),
                ('to',   'bcc')):
            val = parsed_email.get(hdr)
            if not val:
                continue

            kws = set()
            txt = []
            val = val if isinstance(val, list) else [val]
            for addrinfo in val:
                if not isinstance(addrinfo, dict):
                    continue
                txt.extend(addrinfo.values())
                if addrinfo.get('address'):
                    kws.add('%s:%s' % (kw, addrinfo['address']))
                    kws.add('email:%s' % (addrinfo['address'],))

            keywords |= kws
            keywords |= set(kw.split(':')[-1] for kw in kws)

            words = self.words(' '.join(txt), strip_urls=False)
            keywords |= words
            keywords |= set('%s:%s' % (kw, word) for word in words)

        return status, keywords

    def headerprint_keywords(self, parsed_email):
        # FIXME: Should the headerprint be part of the parsed message?
        #        Probably yes, if we intend to use them for more things.
        #
        # These are synthetic keywords which group together messages
        # that have a similar structure or origin. Mostly for use in
        # the spam filters.
        #
        status, keywords = set(), set()
        hp = HeaderPrints(parsed_email)
        for k in ('org', 'sender', 'tools'):
            if k in hp and hp[k]:
                keywords.add('hp_%s:%s' % (k, hp[k]))
        return status, keywords

    def extract_email_keywords(self, metadata, parsed_email):
        """
        The input should be a parsed e-mail, as returned by
        moggie.email.parsemime.

        Returns a tuple of (status-set, keyword-set), where status will
        inform the caller whether additional processing is requested. 
        """
        bt_stat, bt_kws = self.body_text_keywords(parsed_email)
        hd_stat, hd_kws = self.header_keywords(metadata, parsed_email)
        hp_stat, hp_kws = self.headerprint_keywords(parsed_email)

        # FIXME: Look at attachments. Do we want to parse any of them
        #        for keywords? If so we may need to set a status thingo.

        return (
            (bt_stat | hd_stat | hp_stat),
            (bt_kws  | hd_kws  | hp_kws))


if __name__ == '__main__':
    import json
    import sys
    from ..email.parsemime import parse_message

    unittest = (len(sys.argv) <= 1)
    if not unittest:
        if sys.argv[1] == '-':
            msg = bytes(sys.stdin.read(), 'utf-8')
        else:
            msg = open(sys.argv[1], 'rb').read()
    else:
        msg = bytes("""\
From: bre@example.org
To: bre3@example.org, <bre4@example.org> Bjarnzor
Subject: PCR =?utf-8?B?TWFnaWNhbA==?= subject line
Date: Tue, 29 Mar 2022 14:17:00 +0000
Content-Type: multipart/mixed; boundary=1234
Message-Id: <bjarni@mailpile>

--1234
Content-Type: text/plain; charset=utf-8

Halló heimur, þetta er íslenskur texti því stundum þarf að flækja
málin aðeins og athuga hvernig gengur.

Hexadecimal 0x1234 gets ignored yo: 0e1abc 0x123456789

Ég er <bre@example.org> og mailto:bre2@example.org og svo er auðvitað
líka https://www.example.org/foo/bar/baz?bonk vefsíða.

--1234
Content-Type: text/html; charset=utf-8

<script>function() {}</script>
<html>Hello hypertext world</html>

--1234--
""", 'utf-8')

    parsed = parse_message(msg).with_structure().with_text()

    kwe = KeywordExtractor()
    more, keywords = kwe.extract_email_keywords(None, parsed)
    if unittest:
        try:
            assert('msgid:dO8TGE1dMM9XPPoacd35EJIGbXQ' in keywords)
            assert('html' not in keywords)
            assert('html:spooky' in keywords)
            assert('hypertext' in keywords)
            assert('function' not in keywords)
            assert('year:2022' in keywords)
            assert('month:3' in keywords)
            assert('day:29' in keywords)
            assert('date:2022-3-29' in keywords)
            assert('subject:magical' in keywords)
            assert('subject:subject' in keywords)
            assert('pcr' in keywords)
            assert('from:bre' in keywords)
            assert('email:bre@example.org' in keywords)
            assert('email:bre3@example.org' in keywords)
            assert('to:bre3@example.org' in keywords)
            assert('to:bre4@example.org' in keywords)
            assert('to:example' in keywords)
            assert('to:bjarnzor' in keywords)
            assert('from:bjarnzor' not in keywords)
            assert('magical' in keywords)
            assert('halló' in keywords)
            assert('www' in keywords)
            assert('bonk' not in keywords)
            assert('example' in keywords)
            assert('heimur' in keywords)
            assert('org' in keywords)
            assert('has:urls' in keywords)
            assert('has:many_urls' not in keywords)
            assert('is:long' not in keywords)
            assert('bre@example.org' in keywords)
            assert('bre2@example.org' in keywords)
            assert('www.example.org' in keywords)
            assert('er auðvitað' in keywords)
            assert('ignored yo' in keywords)
            assert('0x1234' not in keywords)   # We ignore short hex strings
            assert('0e1abc' not in keywords)   # ditto.
            assert('0x123456789' in keywords)  # Longer ones we do index tho
            assert('þetta er' in keywords)
            assert('og svo' in keywords)
            assert('svo er' in keywords)
            print('Tests passed OK')
        except:
            print('Keywords:\n\t%s' % '\n\t'.join(sorted(list(keywords))))
            raise
    else:
        print('Keywords:\n\t%s' % '\n\t'.join(sorted(list(keywords))))
