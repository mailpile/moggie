# This is a set of tools for parsing basic CSS rules and then emitting
# known-safe equivalents for use in our re-generated HTML.
#
# The strategy:
#    - Using a deliberately incomplete parser, which discards anything not
#      explicitly on our whitelist, we:
#       - Parse and store any top-level style blocks
#       - As we traverse the HTML tree, parse local style declarations
#    - Emit new local style declarations which combine matches from the
#      global styles, with the local ones.
#
# This allows us to preserve styling without ever injecting global CSS
# declarations into the hosting page. It also guarantees that the styles
# we emit are all compliant with our white-list of known safe rules.

import copy
import re
import traceback


class CSSSelector:
    RE_RULEPARTS = re.compile(r'(>|\S[^#\.:\[>]*)')
    RE_ALNUM = re.compile(r'^[a-zA-Z0-9_-]+$')

    def __init__(self, rulestring):
        self.rules = self.make_rules(rulestring)

    def __str__(self):
        def _r(rule):
            rule = list(rule)
            rule.sort(key=lambda e: (
                2 if (e[:1] == '.') else (1 if (e[:1] == '#') else 0)))
            return ''.join(rule)
        return ' '.join(_r(rule) for rule in self.rules)

    def __repr__(self):
        return '<CSSSelector("%s")=%s>' % (str(self), self.rules)

    @classmethod
    def make_rules(cls, rulestring):
        return [
            set(cls.RE_RULEPARTS.findall(rule))
            for rule in rulestring.split()]

    @classmethod
    def describe(cls, element):
        """
        This will take an element triple, as used internally by HTMLCleaner,
        and parse it into a set of attributes CSS might select on.
        """
        tag, attrs = element[:2]
        description = set()
        description.add(tag)
        for a, v in attrs:
            if not v:
                next
            if a == 'class':
                for word in v.split():
                    description.add('.' + word)
            elif a == 'id':
                description.add('#' + v)
            elif cls.RE_ALNUM.match(a):
                description.add('[%s="%s"]' % (a, v.replace('"', '\\"')))
        return description

    def match(self, element_stack, more=None):
        """
        This will check an element stack against our ruleset, returning
        True if it matches, False otherwise.
        """
        rules = more or self.rules
        if not (rules and element_stack):
            return False

        tight, rule = False, rules[-1]
        while (rule == {'>'}):
            rules = rules[:-1]
            if not rules:
                return False
            tight, rule = True, rules[-1]

        for i, element in enumerate(reversed(element_stack)):
            if not (rule - self.describe(element)):
                # Empty set: all criteria match!
                if len(rules) == 1:
                    # This is the only rule, we are done. Success!
                    return True
                elif len(element_stack) <= (i+1):
                    # Have more rules, but out of elements: fail!
                    return False
                else:
                    # OK great, check the next rule.
                    return self.match(element_stack[:-(i+1)], more=rules[:-1])
            elif (not more) or tight:
                # Final rule must match final element to avoid over-matching.
                return False

        return False


class CSSParser:
    STATE_SEL = 'r'
    STATE_STYLES = 's'
    STATE_STYLES_DONE = 'S'
    STATE_COMMENT = 'c'
    STATE_CLINE = 'C'
    STATE_MEDIA = 'm'
    STATE_DONE = None

    RE_END_SEL     = re.compile(r'\s*(\{|/\*|//|@media|$)')
    RE_END_STYLES  = re.compile(r'\s*(\}|/\*|//|@media|$)')
    RE_END_COMMENT = re.compile(r'\s*(\*\/)')
    RE_END_CLINE   = re.compile(r'\s*(\n)')

    def __init__(self):
        self.statemap = {
            self.STATE_SEL: self._parse_selectors,
            self.STATE_STYLES: self._parse_styles,
            self.STATE_STYLES_DONE: self._state_styles_done,
            self.STATE_CLINE: self._parse_cline,
            self.STATE_COMMENT: self._parse_comment}
        self.delimmap = {
            '@media': self.STATE_DONE,  # FIXME: We just stop, which is lame
            '': self.STATE_DONE,
            '{': self.STATE_STYLES,
            '}': self.STATE_STYLES_DONE,
            '//': self.STATE_CLINE,
            '/*': self.STATE_COMMENT}

    def parse_styles(self, style_block):
        return self.parse(style_block, state=self.STATE_STYLES)

    def parse(self, style_block, state=STATE_SEL):
        self.state = state
        self.last_state = None
        self.data = style_block
        self.pos = 0

        self.found_rulesets = 0
        self.selectors = []
        self.styles = []
        while self.state != self.STATE_DONE:
            try:
                self.statemap[self.state]()
            except:
                traceback.print_exc()
                self.state = self.STATE_DONE
        if self.styles:
            self._state_styles_done()

        return self

    def _pb(self, end_re, on_found, next_state):
        next_block = end_re.search(self.data[self.pos:])
        if next_block:
            next_delim = next_block.group(0)
            next_pos = self.pos + next_block.span()[0]
            if next_pos > self.pos:
                on_found(self.data[self.pos:next_pos])
            self.pos = next_pos + len(next_delim)
            self.last_state, self.state = self.state, next_state(next_delim)
        else:
            self.last_state, self.state = self.state, self.STATE_DONE

    def _last_state(self, next_delim):
        return self.last_state

    def _delim_state(self, next_delim):
        return self.delimmap[next_delim.strip()]

    def _parse_selectors(self):
        self._pb(self.RE_END_SEL, self.have_selectors, self._delim_state)

    def _parse_styles(self):
        self._pb(self.RE_END_STYLES, self.have_styles, self._delim_state)

    def _parse_cline(self):
        self._pb(self.RE_END_CLINE, self.have_comment, self._last_state)

    def _parse_comment(self):
        self._pb(self.RE_END_COMMENT, self.have_comment, self._last_state)

    def _state_styles_done(self):
        self.found_rulesets += 1

        selectors = [CSSSelector(sel) for sel in
            (s.strip() for s in ' '.join(self.selectors).strip().split(','))
            if sel]

        styles = [style for style in
            (s.strip() for s in ' '.join(self.styles).strip().split(';'))
            if style]

        if styles:
            self.have_style_rule(selectors, styles)

        self.selectors = []
        self.styles = []
        self.last_state, self.state = self.state, self.STATE_SEL

    def have_selectors(self, data):
        self.selectors.append(data)

    def have_styles(self, data):
        self.styles.append(data)

    def have_style_rule(self, selectors, styles):
        if selectors and styles:
            print('%s { %s; }' % (
                ', '.join(str(rulestring) for s in selectors),
                '; '.join(styles)))

    def have_comment(self, data):
        pass



def _rc(regexp, **flags):
    _re = re.compile(regexp, flags=re.IGNORECASE)
    def _check(a, v):
        v = v.replace('!important', '').strip()
        return (a, v) if _re.match(v) else None
    return _check


class CSSCleaner(CSSParser):
    # This code is not super performant: in the worst case we may need to
    # check all CSS rule-sets against all the tags in a message. So this
    # limit is here to put an upper bound on how much work can be caused by
    # spamming us with complex HTML+CSS.
    MAX_RULES = 150

    CHECK_BCOLLAPSE = _rc(r'^(collapse)$')
    CHECK_COLOR     = _rc(r'^(rgba?\([\d\s\.,]+\)|#[0-9a-f]{3}|#[0-9a-f]{6}|inherit|transparent|white)$')
    CHECK_DIR       = _rc(r'^(inherit|rtl|ltr)$')
    CHECK_DISPLAY   = _rc(r'^(block|inline|inline-block|inline-table|table)$')
    CHECK_LSPACE    = _rc(r'^(normal)$')
    CHECK_NUMBER    = _rc(r'^\d+(\.\d+)?$')
    CHECK_OUTLINE   = _rc(r'^none$')
    CHECK_HALIGN    = _rc(r'^(inherit|left|center|right|justify)$')
    CHECK_FONT_FAM  = _rc(r'^[a-z0-9,\s\'\"-]+$')
    CHECK_FONT_SIZE = _rc(r'^(inherit|-?\d+(\.\d+)?\s*(px|em|%|))$')
    CHECK_FONT_STYL = _rc(r'^(inherit|normal|italic)$')
    CHECK_FONT_WGHT = _rc(r'^(inherit|normal|bold|\d\d\d)$')
    CHECK_SIZE      = _rc(r'^((auto|inherit|-?\d+(\.\d+)?\s*(px|em|%|))\s*){1,4}$')
    CHECK_TEXT_DECO = _rc(r'^(inherit|none|underline)$')
    CHECK_TEXT_TFRM = _rc(r'^(inherit|none|uppercase|lowercase)$')
    CHECK_VALIGN    = _rc(r'^(inherit|top|center|middle|bottom)$')
    CHECK_VISI      = _rc(r'^visible$')  # Disallow hidden
    CHECK_WORDBREAK = _rc(r'^(inherit|normal|break-word)$')
    CHECK_WORDSPACE = _rc(r'^(inherit|normal)$')
    CHECK_ZERO      = _rc(r'^(none|(0\s*(px|em|))*)$')

    ALLOWED_STYLES = {
        'background': CHECK_COLOR,
        'background-color': CHECK_COLOR,
        'border': CHECK_ZERO,
        'border-collapse': CHECK_BCOLLAPSE,
        'border-radius': CHECK_SIZE,
        'border-spacing': CHECK_SIZE,
        'border-width': CHECK_SIZE,
        'color': CHECK_COLOR,
        'direction': CHECK_DIR,
        'display': CHECK_DISPLAY,
        'font-family': CHECK_FONT_FAM,
        'font-style': CHECK_FONT_STYL,
        'font-size': CHECK_FONT_SIZE,
        'font-weight': CHECK_FONT_WGHT,
        'height': CHECK_SIZE,
        'letter-spacing': CHECK_LSPACE,
        'line-height': CHECK_SIZE,
        'margin': CHECK_SIZE,
        'margin-top': CHECK_SIZE,
        'margin-bottom': CHECK_SIZE,
        'margin-left': CHECK_SIZE,
        'margin-right': CHECK_SIZE,
        'max-width': CHECK_SIZE,
        'max-height': CHECK_SIZE,
        'min-width': CHECK_SIZE,
        'min-height': CHECK_SIZE,
        'opacity': CHECK_NUMBER,
        'outline': CHECK_OUTLINE,
        'padding': CHECK_SIZE,
        'padding-top': CHECK_SIZE,
        'padding-bottom': CHECK_SIZE,
        'padding-left': CHECK_SIZE,
        'padding-right': CHECK_SIZE,
        'text-align': CHECK_HALIGN,
        'text-decoration': CHECK_TEXT_DECO,
        'text-transform': CHECK_TEXT_TFRM,
        'vertical-align': CHECK_VALIGN,
        'visibility': CHECK_VISI,
        'width': CHECK_SIZE,
        'word-break': CHECK_WORDBREAK,
        'word-spacing': CHECK_WORDSPACE}

    def __init__(self, checks=None):
        super().__init__()
        self.rule_sets = []
        self.dropped = set()
        self.checks = copy.copy(self.ALLOWED_STYLES)
        if checks:
            self.checks.update(checks)

    def copy(self):
        dup = CSSCleaner(self.checks)
        dup.rule_sets = copy.copy(self.rule_sets)
        dup.dropped = self.dropped
        return dup

    def render_selectors(self, selectors):
        return ',\n'.join(str(s) for s in selectors)

    def render_styles(self, styles):
        return '; '.join('%s:%s' % (s, v) for s, v in styles) + ';'

    def render_rule_sets(self, rule_sets):
        def _p(selectors, styles):
            if selectors:
                return ('%s {\n  %s }' % (
                    self.render_selectors(selectors),
                    self.render_styles(styles)))
            else:
                return self.render_styles(styles)

        return '\n'.join(_p(sels, styles) for sels, styles in rule_sets)

    def __str__(self):
        return self.render_rule_sets(self.rule_sets)

    def render_report(self):
        return ("""
/* Made less spooky by moggie.security.css.CSSCleaner

  * Active CSS rulesets: %d
  * Dropped:%s

*/""" % (
            len(self.rule_sets),
            ''.join('\n    * %s' % d for d in self.dropped)))

    def clean_styles(self, styles):
        for style in styles:
            try:
                a, v = style.split(':', 1)
                a, v = a.lower().strip(), v.strip()
                a, v = self.checks.get(a)(a, v)
                yield (a, v)
            except:
                if style[:2] not in ('ms', '--', '-w', '-m'):
                    # Make a note of dropped non-vendor-specific styles
                    self.dropped.add(style)

    def clean_selectors(self, selectors):
        return selectors

    def have_comment(self, comment):
        self.dropped.add('// ' + comment.strip())

    def have_style_rule(self, selectors, styles):
        if len(self.rule_sets) < self.MAX_RULES:
            self.rule_sets.append((
                list(self.clean_selectors(selectors)),
                list(self.clean_styles(styles))))

    def apply_styles(self, element_stack):
        # FIXME: Make this smarter, faster?
        found_styles = {}
        for selectors, styles in self.rule_sets:
            if not selectors:
                for s, v in styles:
                    found_styles[s] = v
            for sel in selectors:
                if sel.match(element_stack):
                    for s, v in styles:
                        found_styles[s] = v
                    break
        if found_styles:
            return self.render_styles(found_styles.items())
        else:
            return ''

if __name__ == "__main__":
    TEST_SIMPLE = 'color: #fff; evil: junk; font-size:1px'
    TEST_STYLES = """
        /* This is some junk */
        div.fancy,  // More junk!
        td.ugly {color: /*ohai*/ #000; top:1; } /* hello world */
        tr .nice {
           font-size: 1px;     // this is a oneliner
           bottom: 2px;}}}}"""

    simple = CSSCleaner().parse_styles(TEST_SIMPLE)
    assert(str(simple) == 'color: #fff; font-size: 1px;')

    fancy = CSSCleaner().parse(TEST_STYLES)
    print('%s%s' % (fancy, fancy.render_report()))
    assert(len(fancy.dropped) == 7)
    applied = fancy.copy().parse_styles('width: 10px').apply_styles([
        ('table', []),
        ('tr', []),
        ('td', [('class', 'ugly nice')])])
    assert(applied == 'color: #000; font-size: 1px; width: 10px;')


    class MockCSSParser(CSSParser):
        def have_style_rule(self, sels, styles):
            sels.sort(key=lambda k: str(k))
            styles.sort()
            if (len(sels) == 2) and sels[-1].rules == [{'td', '.ugly'}]:
                assert(styles == ['color:  #000', 'top:1'])
            elif (len(sels) == 1) and sels[0].rules == [{'tr'}, {'.nice'}]:
                assert(styles == ['bottom: 2px', 'font-size: 1px'])
            else:
                assert(not 'invalid ruleset')

    mcsp = MockCSSParser()
    mcsp.parse(TEST_STYLES)
    assert(mcsp.found_rulesets == 2)


    elems = [
        ('div', [('id', 'top'), ('class', 'fun')], ''),
        ('div', [('id', 'middle'), ('class', 'sadness')], ''),
        ('p',   [('foo', 'bar'), ('class', 'para bork')], '')]

    c = CSSSelector('td.bar p[foo="bar"]')
    assert(c.rules == [{'td', '.bar'}, {'p', '[foo="bar"]'}])
    assert(c.describe(elems[-1]) == {'p', '.para', '.bork', '[foo="bar"]'})

    assert(not CSSSelector('div').match(elems))
    assert(    CSSSelector('p').match(elems))
    assert(    CSSSelector('div p').match(elems))
    assert(not CSSSelector('div#top > p').match(elems))
    assert(    CSSSelector('div#middle > p').match(elems))
    assert(    CSSSelector('div div p').match(elems))
    assert(not CSSSelector('div div div p').match(elems))
    assert(    CSSSelector('#top p').match(elems))
    assert(    CSSSelector('#top #middle p').match(elems))
    assert(not CSSSelector('#middle #top p').match(elems))
    assert(    CSSSelector('div#top p').match(elems))
    assert(not CSSSelector('p#top p').match(elems))
    assert(    CSSSelector('#top p.para').match(elems))
    assert(    CSSSelector('#top .para').match(elems))
    assert(not CSSSelector('#top p.woop').match(elems))

    print('Tests passed OK')

