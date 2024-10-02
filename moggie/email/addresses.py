# vim: set fileencoding=utf-8 :
#
# FIXME: Should we do a better job handling the group construct?
#        At least recognize when e-mails belong to a group and record that?
#        Or does it just not matter at all in practice?
#
import base64
import quopri
import re

from .rfc2074 import rfc2074_unquote


class AddressInfo(dict):
    def __init__(self, address, fn, keys=None):
        dict.__init__(self)
        self.update({
            'address': address,
            'fn': fn})
        if keys is not None:
            self['keys'] = keys

    fn = property(
        lambda s: s.get('fn'),
        lambda s,v: s.__setitem__('fn', v))

    address = property(
        lambda s: s.get('address'),
        lambda s,v: s.__setitem__('address', v))

    comment = property(
        lambda s: s.get('comment'),
        lambda s,v: s.__setitem__('comment', v))

    keys = property(
        lambda s: s.get('keys', []),
        lambda s,v: s.__setitem__('keys', v))

    def normalized(self, **kwargs):
        return AddressHeaderParser.normalized_addresses([self], **kwargs)[0]

    def __str__(self):
        return self.normalized()


class AddressHeaderParser(list):
    """
    This is a class which tries very hard to interpret the From:, To:
    and Cc: lines found in real-world e-mail and make sense of them.

    The general strategy of this parser is to:
       1. parse header data into tokens
       2. group tokens together into address + name constructs.

    And optionaly,
       3. normalize each group to a standard format

    In practice, we do this in multiple passes: first a strict pass where
    we try to parse things semi-sensibly, followed by fuzzier heuristics.

    Ideally, if folks format things correctly we should parse correctly.
    But if that fails, there are are other passes where we try to cope
    with various types of weirdness we've seen in the wild. The wild can
    be pretty wild.

    This parser is NOT (yet) fully RFC2822 compliant - in particular it
    will get confused by nested comments (see FIXME in tests below).

    The normalization will take pains to ensure that < and , are never
    present inside a name/comment (even if legal), to make life easier
    for lame parsers down the line.

    Examples:

    >>> ahp = AddressHeaderParser(AddressHeaderParser.TEST_HEADER_DATA)
    >>> ai = ahp[1]
    >>> ai.fn
    'Bjarni'
    >>> ai.address
    'bre@example.org'
    >>> ahpn = ahp.normalized()
    >>> (ahpn == ahp.TEST_EXPECT_NORMALIZED_ADDRESSES) or ahpn
    True

    >>> AddressHeaderParser('Weird email@somewhere.com Header').normalized()[0]
    '"Weird Header" <email@somewhere.com>'

    >>> ai = AddressHeaderParser(data=ahp.TEST_BYTES_DATA)
    >>> ai[0].fn
    'Bjarni R\\xfanar'
    >>> ai[0].fn == ahp.TEST_BYTES_NAME
    True
    >>> ai[0].address
    'b@c.x'
    """

    TEST_BYTES_DATA = b'Bjarni R\xfanar <b@c.x#61A015763D28D4>'
    TEST_BYTES_NAME = 'Bjarni Rúnar'
    TEST_HEADER_DATA = """
        bre@example.org  ,
        bre@example.org Bjarni ,
        bre@example.org bre@example.org,
        bre@example.org (bre@notmail.com),
        "<bre@notmail.com>" <bre@example.org>,
        =?utf-8?Q?=3Cbre@notmail.com=3E?= <bre@example.org>,
        bre@example.org ((nested) bre@notmail.com comment),
        (FIXME: (nested) bre@wrongmail.com parser breaker) bre@example.org,
        undisclosed-recipients-gets-ignored:,
        Bjarni [mailto:bre@example.org],
        "This is a key test" <bre@example.org#61A015763D28D410A87B197328191D9B3B4199B4>,
        bre@example.org (Bjarni Runar Einar's son);
        Bjarni =?iso-8859-1?Q?i?=s bre @example.org,
        Bjarni =?iso-8859-1?Q?Runar?=Einarsson<' bre'@ example.org>,
        "Einarsson, Bjarni" <bre@example.org>,
        =?iso-8859-1?Q?Lonia_l=F6gmannsstofa?= <lonia@example.com>,
        "Bjarni @ work" <bre@example.com>,
    """

    TEST_EXPECT_NORMALIZED_ADDRESSES = [
        '<bre@example.org>',
        '"Bjarni" <bre@example.org>',
        '"bre@example.org" <bre@example.org>',
        '"bre@notmail.com" <bre@example.org>',
        '=?utf-8?Q?=3Cbre@notmail.com=3E?= <bre@example.org>',
        '=?utf-8?Q?=3Cbre@notmail.com=3E?= <bre@example.org>',
        '"(nested bre@notmail.com comment)" <bre@example.org>',
        '"(FIXME: nested parser breaker) bre@example.org" <bre@wrongmail.com>',
        '"Bjarni" <bre@example.org>',
        '"This is a key test" <bre@example.org>',
        '"Bjarni Runar Einar\\\'s son" <bre@example.org>',
        '"Bjarni i s" <bre@example.org>',
        '"Bjarni Runar Einarsson" <bre@example.org>',
        '=?utf-8?Q?Einarsson=2C_Bjarni?= <bre@example.org>',
        '=?utf-8?Q?Lonia_l=C3=B6gmannsstofa?= <lonia@example.com>',
        '"Bjarni @ work" <bre@example.com>']


    # Escaping and quoting
    TXT_RE_QUOTE = '=\\?([^\\?\\s]+)\\?([QqBb])\\?([^\\?\\s]*)\\?='
    TXT_RE_QUOTE_NG = TXT_RE_QUOTE.replace('(', '(?:')
    RE_ESCAPES = re.compile('\\\\([\\\\"\'])')
    RE_QUOTED = re.compile(TXT_RE_QUOTE)
    RE_SHOULD_ESCAPE = re.compile('([\\\\"\'])')
    RE_SHOULD_QUOTE = re.compile('[^a-zA-Z0-9()\\.:/_ \'"+@-]')

    # This is how we normally break a header line into tokens
    RE_TOKENIZER = re.compile('(<[^<>]*>'                    # <stuff>
                              '|\\([^\\(\\)]*\\)'            # (stuff)
                              '|\\[[^\\[\\]]*\\]'            # [stuff]
                              '|"(?:\\\\\\\\|\\\\"|[^"])*"'  # "stuff"
                              "|'(?:\\\\\\\\|\\\\'|[^'])*'"  # 'stuff'
                              '|' + TXT_RE_QUOTE_NG +        # =?stuff?=
                              '|,'                           # ,
                              '|;'                           # ;
                              '|\\s+'                        # white space
                              '|[^\\s;,]+'                   # non-white space
                              ')')

    # Where to insert spaces to help the tokenizer parse bad data
    RE_MUNGE_TOKENSPACERS = (re.compile('(\\S)(<)'), re.compile('(\\S)(=\\?)'))

    # Characters to strip aware entirely when tokenizing munged data
    RE_MUNGE_TOKENSTRIPPERS = (re.compile('[<>"]'),)

    # This is stuff we ignore (undisclosed-recipients, etc)
    RE_IGNORED_GROUP_TOKENS = re.compile('(?i)undisclosed')

    # Things we strip out to try and un-mangle e-mail addresses when
    # working with bad data.
    RE_MUNGE_STRIP = re.compile('(?i)(?:\\bmailto:|[\\s"\']|\\?$)')

    # This a simple regular expression for detecting e-mail addresses.
    RE_MAYBE_EMAIL = re.compile('^[^()<>@,;:\\\\"\\[\\]\\s\000-\031]+'
                                '@[a-zA-Z0-9_\\.-]+(?:#[A-Za-z0-9]+)?$')

    # We try and interpret non-ascii data as a particular charset, in
    # this order by default. Should be overridden whenever we have more
    # useful info from the message itself.
    DEFAULT_CHARSET_ORDER = ('utf-8', 'iso-8859-1')

    def __init__(self, data=None, charset_order=None, **kwargs):
        self.charset_order = charset_order or self.DEFAULT_CHARSET_ORDER
        self._parse_args = kwargs
        if data is None:
            self._reset(**kwargs)
        else:
            if isinstance(data, bytes):
                for cs in self.charset_order:
                    try:
                        data = str(data, cs)
                        break
                    except UnicodeDecodeError:
                        pass
            self.parse(data)

    def _reset(self, _raw_data=None, strict=False, _raise=False):
        self._raw_data = _raw_data
        self._tokens = []
        self._groups = []
        self[:] = []

    def parse(self, data):
        return self._parse(data, **self._parse_args)

    def _parse(self, data, strict=False, _raise=False):
        self._reset(_raw_data=data)

        # 1st pass, strict
        try:
            self._tokens = self._tokenize(self._raw_data)
            self._groups = self._group(self._tokens)
            self[:] = self._find_addresses(self._groups,
                                           _raise=(not strict))
            return self
        except ValueError:
            if strict and _raise:
                raise
        if strict:
            return self

        # 2nd & 3rd passes; various types of sloppy
        for _pass in ('2', '3'):
            try:
                self._tokens = self._tokenize(self._raw_data, munge=_pass)
                self._groups = self._group(self._tokens, munge=_pass)
                self[:] = self._find_addresses(self._groups,
                                               munge=_pass,
                                               _raise=_raise)
                return self
            except ValueError:
                if _pass == '3' and _raise:
                    raise
        return self

    @classmethod
    def unescape(self, string):
        return re.sub(self.RE_ESCAPES, lambda m: m.group(1), string)

    @classmethod
    def escape(self, strng):
        return re.sub(self.RE_SHOULD_ESCAPE, lambda m: '\\'+m.group(0), strng)

    @classmethod
    def quote(self, strng):
        if re.search(self.RE_SHOULD_QUOTE, strng):
            enc = quopri.encodestring(strng.encode('utf-8'), False,
                                      header=True)
            enc = enc.replace(b'<', b'=3C').replace(b'>', b'=3E')
            enc = enc.replace(b',', b'=2C')
            return '=?utf-8?Q?%s?=' % str(enc, 'latin-1')
        else:
            return '"%s"' % self.escape(strng)

    def _tokenize(self, string, munge=False):
        if munge:
            for ts in self.RE_MUNGE_TOKENSPACERS:
                string = re.sub(ts, '\\1 \\2', string)
            if munge == 3:
                for ts in self.RE_MUNGE_TOKENSTRIPPERS:
                    string = re.sub(ts, '', string)
        return re.findall(self.RE_TOKENIZER, string)

    def _clean(self, token):
        if token[:1] in ('"', "'"):
            if token[:1] == token[-1:]:
                return self.unescape(token[1:-1])
        elif token.startswith('[mailto:') and token[-1:] == ']':
            # Just convert [mailto:...] crap into a <address>
            return '<%s>' % token[8:-1]
        elif (token[:1] == '[' and token[-1:] == ']'):
            return token[1:-1]
        return token

    def _group(self, tokens, munge=False):
        groups = [[]]
        for token in tokens:
            token = token.strip()
            if token in (',', ';'):
                # Those tokens SHOULD separate groups, but we don't like to
                # create groups that have no e-mail addresses at all.
                if groups[-1]:
                    if [g for g in groups[-1] if '@' in g]:
                        groups.append([])
                        continue
                    # However, this stuff is just begging to be ignored.
                    elif [g for g in groups[-1]
                          if re.match(self.RE_IGNORED_GROUP_TOKENS, g)]:
                        groups[-1] = []
                        continue
            if token:
                groups[-1].append(rfc2074_unquote(self._clean(token)))
        if not groups[-1]:
            groups.pop(-1)
        return groups

    def _find_addresses(self, groups, **fa_kwargs):
        alist = [self._find_address(g, **fa_kwargs) for g in groups]
        return [a for a in alist if a]

    def _find_address(self, g, _raise=False, munge=False):
        if g:
            g = g[:]
        else:
            return []

        def email_at(i):
            for j in range(0, len(g)):
                if g[j][:1] == '(' and g[j][-1:] == ')':
                    g[j] = g[j][1:-1]
            rest = ' '.join([g[j] for j in range(0, len(g))
                             if (j != i) and g[j]
                             ]).replace(' ,', ',').replace(' ;', ';')
            email, keys = g[i], None
            if '#' in email[email.index('@'):]:
                email, key = email.rsplit('#', 1)
                keys = [{'fingerprint': key}]
            return AddressInfo(email, rest.strip(), keys=keys)

        def munger(string):
            if munge:
                return re.sub(self.RE_MUNGE_STRIP, '', string)
            else:
                return string

        # If munging, look for email @domain.com in two parts, rejoin
        if munge:
            for i in range(0, len(g)):
                if i > 0 and i < len(g) and g[i][:1] == '@':
                    g[i-1:i+1] = [g[i-1]+g[i]]
                elif i < len(g)-1 and g[i][-1:] == '@':
                    g[i:i+2] = [g[i]+g[i+1]]

        # 1st, look for <email@domain.com>
        #
        # We search from the end, to make the algorithm stable in the case
        # that the name part also starts with a < (is that allowed?).
        #
        for i in reversed(range(0, len(g))):
            if g[i][:1] == '<' and g[i][-1:] == '>':
                maybemail = munger(g[i][1:-1])
                if re.match(self.RE_MAYBE_EMAIL, maybemail):
                    g[i] = maybemail
                    return email_at(i)

        # 2nd, look for bare email@domain.com
        for i in range(0, len(g)):
            maybemail = munger(g[i])
            if re.match(self.RE_MAYBE_EMAIL, maybemail):
                g[i] = maybemail
                return email_at(i)

        if _raise:
            raise ValueError('No email found in %s' % (g,))
        else:
            return None

    def addresses_list(self, with_keys=False):
        addresses = []
        for addr in self:
            m = addr.address
            if with_keys and addr.keys:
                m += "#" + addr.keys[0].get('fingerprint')
            addresses.append(m)
        return addresses

    @classmethod
    def normalized_addresses(cls,
                             addresses, quote=True, with_keys=False,
                             force_name=False):
        if not addresses:
            return []
        def fmt(ai):
            email = ai.address
            if with_keys and ai.keys:
                fp = ai.keys[0].get('fingerprint')
                epart = '<%s%s>' % (email, fp and ('#%s' % fp) or '')
            else:
                epart = '<%s>' % email
            if ai.fn:
                 return ' '.join([quote and cls.quote(ai.fn) or ai.fn, epart])
            elif force_name:
                 return ' '.join([quote and cls.quote(email) or email, epart])
            else:
                 return epart
        return [fmt(ai) for ai in addresses]

    def normalized(self, **kwargs):
        return self.normalized_addresses(self, **kwargs)


if __name__ == "__main__":
    import doctest
    import sys
    results = doctest.testmod(optionflags=doctest.ELLIPSIS,
                              extraglobs={})

    ahp = AddressHeaderParser('Bjarni <bre@example.org>')
    assert(str(ahp[0]) == '"Bjarni" <bre@example.org>')

    print('%s' % (results, ))
    if results.failed:
        sys.exit(1)
