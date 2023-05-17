import copy
import re
import hashlib

from html.parser import HTMLParser
from moggie.security.mime import magic_part_id


class HTMLCleaner(HTMLParser):
    """
    This class will attempt to consume an HTML document and emit a new
    one which is roughly equivalent, but only has known-safe tags and
    attributes. The output is also guaranteed to not have nesting errors.

    It will strip (and count) potentially dangerous things like scripts
    and misleading links.

    It also generates keywords/fingerprints describing some technical
    features of the HTML, for use in the search engine and spam filters.

    FIXME:
       * Parse style sheets and convert them into style='' attributes.
       * Parse color and font size statements to prevent thing from being
         made invisible. Set a warning flag if we see this.
       * Callbacks when we see a references to attached images?
       * Do something smart when we see links?
    """
    ALLOW = lambda v: True
    RE_WEBSITE = re.compile('(https?:/+)?(([a-z0-9]+\.[a-z0-9]){2,}[a-z0-9]*)')
    CHECK_TARGET = re.compile('^(_blank)$').match
    CHECK_VALIGN = re.compile('^(top|bottom|center)$').match
    CHECK_HALIGN = re.compile('^(left|right|center)$').match
    CHECK_DIGIT = re.compile('^\d+$').match
    CHECK_SIZE = re.compile('^\d+(%|px)?$').match
    CHECK_LANG = re.compile('^[a-zA-Z-]+$').match
    CHECK_DIR = re.compile('^(ltr|rtl)$').match
    CHECK_CLASS = re.compile('^(mHtmlBody|mRemoteImage|mInlineImage|mso[a-z]+|wordsection\d+)$', re.IGNORECASE).match
    ALLOWED_ATTRIBUTES = {
        'alt':         ALLOW,
        'title':       ALLOW,
        'href':        ALLOW,  # FIXME
        'src':         ALLOW,  # FIXME
        'data-m-src':  ALLOW,  # We generate this to replace img src= attributes
        'name':        ALLOW,
        'target':      CHECK_TARGET,
        'bgcolor':     re.compile(r'^([a-zA-Z]+|\#[0-9a-f]+)$').match,
        'class':       CHECK_CLASS,
        'dir':         CHECK_DIR,
        'lang':        CHECK_LANG,
        'align':       CHECK_HALIGN,
        'valign':      CHECK_VALIGN,
        'width':       CHECK_SIZE,
        'height':      CHECK_SIZE,
        'border':      CHECK_DIGIT,
        'colspan':     CHECK_DIGIT,
        'rowspan':     CHECK_DIGIT,
        'cellspacing': CHECK_DIGIT,
        'cellpadding': CHECK_DIGIT}

    PROCESSED_TAGS = set([
        # We process these, so we can suppress them!
        'head', 'style', 'script',
        # These get rewritten to something else
        'body',
        # These are tags we pass through
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'br',
        'div', 'span', 'p', 'a', 'img', 'blockquote', 'pre',
        'table', 'thead', 'tbody', 'tr', 'th', 'td', 'ul', 'ol', 'li',
        'b', 'i', 'tt', 'center', 'strong', 'em', 'small', 'smaller', 'big'])

    SUPPRESSED_TAGS = set(['head', 'script', 'style', 'moggie_defanged'])
    DANGEROUS_TAGS = set(['script'])

    SINGLETON_TAGS = set(['hr', 'img', 'br'])
    CONTAINER_TAGS = set(['html', 'body', 'table', 'ul', 'ol', 'div'])
    TAGS_END_P = set([
        'table', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    SELF_NESTING = set([
        'div', 'span',
        'b', 'i', 'tt', 'center', 'strong', 'em', 'small', 'smaller', 'big'])

    def __init__(self,
            data=None, callbacks=None, css_cleaner=None, stop_after=None):
        super().__init__()
        self.cleaned = []
        self.keywords = set([])
        self.tag_stack = []
        self.tags_seen = []
        self.dropped_tags = []
        self.dropped_attrs = set()
        self.a_hrefs = []
        self.img_srcs = []
        self.attribute_checks = copy.copy(self.ALLOWED_ATTRIBUTES)
        self.stop_after = stop_after

        self.css_cleaner = css_cleaner
        if css_cleaner:
            self.attribute_checks['style'] = lambda v: True

        self.builtins = {
            'body': lambda s,t,a,b: ('div', s._aa(a, 'class', 'mHtmlBody'), b),
            'a': self._clean_tag_a,
            'img': self._clean_tag_img}
        self.callbacks = callbacks or {}

        self.force_closed = 0
        self.saw_danger = 0

        if data:
            self.feed(data)

    def _aa(self, attrs, attr, value):
        """
        Append a value to an attribute, or set it. Used to add classes to tags.
        """
        for i, (a, v) in enumerate(attrs):
            if a == attr:
                if v:
                    attrs[i] = (a, v + ' ' + value)
                else:
                    attrs[i] = (a, value)
                return attrs
        return attrs + [(attr, value)]

    def _parent_tags(self):
        return [t for t, a, b in self.tag_stack]

    def _container_tags(self, parent_tags=None):
        parent_tags = parent_tags or self._parent_tags()
        i = len(parent_tags)-1
        while (i > 0) and (parent_tags[i] not in self.CONTAINER_TAGS):
            i -= 1
        return parent_tags[i:]

    def _quote(self, t):
        return (
            t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))

    def _quote_attr(self, t):
        return '"%s"' % self._quote(t).replace('"', '&quot;')

    def handle_decl(self, decl):
        self.tags_seen.append(decl)

    def handle_starttag(self, tag, attrs):
        # FIXME: Does this tag imply we open a container of some sort?
        #        Is that ever a thing?

        tag = tag.split(':', 1)[-1]   # FIXME: Handle namespaces better? No?
        if tag not in self.tags_seen:
            self.tags_seen.append(tag)
        if tag in self.DANGEROUS_TAGS:
            self.saw_danger += 1
        if tag not in self.PROCESSED_TAGS:
            self.dropped_tags.append(tag)
            return

        if self.stop_after is not None:
            if self.stop_after < 0:
                attrs = [('style', 'display:none;')]

        container = self._container_tags()

        # Does this tag imply we should close previous ones?
        if tag not in self.SELF_NESTING and tag in container:
            while self.tag_stack:
                closing = self.tag_stack[-1][0]
                if closing not in ('p', 'li'):
                    # Bare <p> and <li> are common enough to not count
                    self.force_closed += 1
                self.handle_endtag(closing)
                if closing == tag:
                    break

        # Close dangling paragraphs
        elif tag in self.TAGS_END_P and 'p' in container:
            while self.tag_stack:
                closing = self.tag_stack[-1][0]
                if closing not in ('p', 'li'):
                    # Bare <p> and <li> are common enough to not count
                    self.force_closed += 1
                self.handle_endtag(closing)
                if closing == 'p':
                    break

        # FIXME? Sanitize attributes
        self.tag_stack.append([tag, attrs, ''])
        if tag in self.SINGLETON_TAGS:
            self.handle_endtag(tag)

    def _clean_tag_a(self, _, t, attrs, b):
        """
        """
        def _tagless(c):
            return re.sub(r'<[^>]+>', '', c[:80])

        m = self.RE_WEBSITE.match(_tagless(b).lower())
        page_domain = m.group(2) if m else None

        danger = []
        for i, (a, v) in enumerate(attrs):
            if (a == 'href') and v:
                # FIXME: Should we just rewrite all href= links into something
                #        which requires Javascript to undo? This IS how users
                #        get phished, so extra steps here are justified.
                self.a_hrefs.append(v)
                ok, parts = True, v.split('/')
                if not (parts and parts[0] in (
                        'http:', 'https:', 'mailto:', 'cid:')):
                    # Invailid proto, distrust
                    ok = False

                if ok and page_domain:
                    # If domain is in description, make sure it matches URL
                    dp = 2 if parts[0] in ('http:', 'https:') else 1
                    ok = parts[dp].endswith(page_domain)

                if ok and parts[0] in ('http:', 'https:'):
                    # HTTP and HTTPS URLs should not have @-signs in the
                    # hostname part, that's both a very common phishing
                    # technique, and a bad practice otherwise.
                    ok = (len(parts) > 2) and ('@' not in parts[2])

                if not ok:
                    danger.append(i)
        if danger:
            for i in reversed(danger):
                a, v = attrs.pop(i)
                self.dropped_attrs.add((t, a, v))
            self.saw_danger += 1
        return t, attrs, b

    def _clean_tag_img(self, _, t, attrs, b):
        remote = False
        inline = False
        for i, (a, v) in enumerate(attrs):
            if v and (a == 'src'):
                self.img_srcs.append(v)
                attrs[i] = ('data-m-src', v)
                inline = (v[:4] == 'cid:')
                remote = not inline
        if remote:
            return t, self._aa(attrs, 'class', 'mRemoteImage'), b
        elif inline:
            return t, self._aa(attrs, 'class', 'mInlineImage'), b
        else:
            return t, attrs, b

    def _clean_attributes(self, tag, attrs):
        saw_style = False
        css_cleaner = self.css_cleaner
        for a, v in attrs:
            if a.startswith('on'):
                self.saw_danger += 1
            validator = self.attribute_checks.get(a)
            try:
                if validator and validator(v):
                    if css_cleaner and (a == 'style'):
                        c = css_cleaner.copy().parse_styles(v)
                        v = c.apply_styles(self.tag_stack)
                        saw_style = True
                    if v:
                        yield a, v
                else:
                    self.dropped_attrs.add((tag, a, v))
            except (ValueError, TypeError):
                self.dropped_attrs.add((tag, a, v))
        if css_cleaner and not saw_style:
            style = css_cleaner.apply_styles(self.tag_stack)
            if style:
                yield ('style', style)

    def _render_attrs(self, attrs):
        return ''.join(' %s=%s' % (a, self._quote_attr(v))
            for a, v in attrs if (a and (v is not None)))

    def handle_endtag(self, tag):
        if not self.tag_stack:
            return

        tag = tag.split(':', 1)[-1]   # FIXME: Handle namespaces better? No?
        if tag != self.tag_stack[-1][0]:
            if tag in self._parent_tags()[:-1]:
                while tag != self.tag_stack[-1][0]:
                    self.force_closed += 1
                    self.handle_endtag(self.tag_stack[-1][0])

        if tag == self.tag_stack[-1][0]:
            t, a, b = self.tag_stack[-1]
            for cbset in (self.builtins, self.callbacks):
                cb = cbset.get(t)
                if (cb is not None) and (t not in self.SUPPRESSED_TAGS):
                    t, a, b = cb(self, t, a, b)

            if (t == 'style') and self.css_cleaner:
                self.css_cleaner.parse(b)

            if t and t in self.SUPPRESSED_TAGS:
                self.dropped_tags.append(tag)
                self.tag_stack.pop(-1)
                return

            if not t:
                self.tag_stack.pop(-1)
                return

            regenerated = self.rerender_tag(t, a, b)
            if self.stop_after is not None:
                self.stop_after -= len(regenerated)

            self.tag_stack.pop(-1)
            if self.tag_stack:
                self.tag_stack[-1][-1] += regenerated
            else:
                self.cleaned.append(regenerated)

    def rerender_tag(self, t, a, b):
        # Note: this depends on self.tag_stack being intact for
        #       correct application of global styles.
        a = list(self._clean_attributes(t, a))
        if 'display:none;' in dict(a).get('style', ''):
            return ''

        a = self._render_attrs(a)
        if t in self.SINGLETON_TAGS:
            return '<%s%s>' % (t, a)
        else:
            return '<%s%s>%s</%s>' % (t, a, b, t)

    def handle_data(self, data):
        """
        Pass through any data parts, but ensure they are properly quoted.
        This should guarantee that anything not recognized as a tag by the
        HTMLParser won't be recognized as a tag downstream either.
        """
        if not data:
            return

        def _callbacks(t, a, d):
            for cbset in (self.builtins, self.callbacks):
                cb = cbset.get('DATA')
                if (cb is not None) and (t not in self.SUPPRESSED_TAGS):
                    rv = cb(t, a, d)
                    if rv is not None:
                        d = rv
            return d

        if self.tag_stack:
            t, a, _ = self.tag_stack[-1]
            self.tag_stack[-1][-1] += self._quote(_callbacks(t, a, data))
        else:
            self.cleaned.append(self._quote(_callbacks(None, None, data)))

    def close(self):
        super().close()
        # Close any dangling tags.
        while self.tag_stack:
            self.force_closed += 1
            self.handle_endtag(self.tag_stack[-1][0])
        self._make_html_keywords()
        return ''.join(t for t in self.cleaned if t).strip()

    def report(self):
        return """\
<!-- Made less spooky by moggie.security.html.HTMLCleaner

  * Spooky content: %d
  * Force-closed tags: %d
  * Keywords: %s
  * Link count: %d
  * Image count: %d
  * Encountered tags: %s
  * Dropped tags: %s
  * Dropped attributes: %s
%s
-->"""  %  (self.saw_danger, self.force_closed,
            ', '.join(self.keywords),
            len(set(self.a_hrefs)),
            len(set(self.img_srcs)),
            ', '.join(self._quote(t) for t in self.tags_seen),
            ', '.join(self._quote(t) for t in self.dropped_tags),
            ''.join('\n    * (%s) %s=%s'
                % (da[0], self._quote(da[1] or ''), self._quote(da[2] or ''))
                for da in self.dropped_attrs),
            self._quote(self.css_cleaner.render_report())
                if self.css_cleaner else '')

    def _make_html_keywords(self):
        def _h16(stuff):
            return hashlib.md5(bytes(stuff, 'utf-8')).hexdigest()[:12]
        inline_images = len([i for i in self.img_srcs if i[:4] == 'cid:'])
        remote_images = len([i for i in self.img_srcs if i[:4] != 'cid:'])
        self.keywords.add(
            'html:code-%s' % ''.join([
                'd' if self.saw_danger else '',
                'f' if self.force_closed else '',
                'a' if self.dropped_attrs else '',
                'm' if (len(self.a_hrefs) + len(self.img_srcs)) > 10 else '',
                'l' if self.a_hrefs else '',
                'i' if self.img_srcs else '',
                'i' if inline_images else '']))
        self.keywords.add(
            'html:tags-%x-%s' % (
                len(self.dropped_tags),
                _h16(','.join(self.tags_seen))))
        if self.saw_danger:
            self.keywords.add('html:spooky')
        if self.img_srcs:
            self.keywords.add('html:images')
        if self.a_hrefs:
            self.keywords.add('html:links')
        if inline_images:
            self.keywords.add('html:inline-img')
        if remote_images:
            self.keywords.add('html:remote-img')

    def clean(self):
        self.close()
        return (
            ''.join(t for t in self.cleaned if t).strip() +
            '\n' + self.report())


class HTMLToTextCleaner(HTMLCleaner):
    def __init__(self, html, **kwargs):
        self.html = html
        self.wrap = int(kwargs.get('wrap', 72))
        self.all_links = kwargs.get('all_links', False)
        self.all_images = kwargs.get('all_images', False)
        self.no_images = kwargs.get('no_images', False)

        for k in ('all_links', 'all_images', 'no_images', 'wrap'):
            if k in kwargs:
                del kwargs[k]

        super().__init__(data=html, **kwargs)

    def _wrap_text(self, txt, wrap=None):
        wrap = wrap or self.wrap
        lines = []
        for chunk in txt.splitlines():
            lines.append('')
            for word in chunk.replace('\r', '').split():
                if len(lines[-1]) + len(word) >= wrap:
                    if lines[-1]:
                        lines.append('')
                if lines[-1]:
                    lines[-1] += ' ' + word
                else:
                    lines[-1] += word
        return '\n'.join(lines)

    def handle_data(self, data):
        if not data:
            data = ''

        # FIXME: This needs to be done differently in order to support
        #        tables, since the table gets to place the text fragments
        #        in cells - so we can't entirely flatten the structure.

        if self.tag_stack:
            t, a, _ = lts = self.tag_stack[-1]
            if t == 'pre':
                lts[-1] += data
            else:
                html = re.sub(r'\s+', ' ', data.lstrip(), flags=re.S)
                lts[-1] += html

        elif data:
            self.cleaned.append(data)

    def rerender_tag(self, t, a, b):
        indents = {
            'blockquote': 2,
            'li': 2,
            'ul': 3,
            'ol': 3}
        wrap = self.wrap
        for tag, _, _ in self.tag_stack:
            # FIXME: need different calculations for cells within a table.
            wrap -= indents.get(tag, 0)

        if t in ('script', 'style'):
            return ''

        def _strips(txt):
            return re.sub(
                    r'\n\s+\n', '\n\n', re.sub(r'^\s*\n', '', txt
                )).rstrip()

        def _alt(attr):
            alt = adict.get(attr)
            if alt and alt.startswith('http'):
                return ''
            return alt

        adict = dict(self._clean_attributes(t, a))
        if 'display:none;' in adict.get('style', ''):
            return ''

        if t == 'hr':
            return '\n%s\n' % ('-' * wrap)
        if t == 'br':
            return '\n'
        if t in ('b', 'strong'):
            return (' **%s** ' % b.strip()) if b else ''
        if t == ('i', 'em'):
            return (' *%s* ' % b.strip()) if b else ''

        if t == 'img':
            src = adict.get('data-m-src', '')
            txt = (_alt('alt') or _alt('title') or '').strip()
            br = '\n' if (len(src) + len(txt) > self.wrap/2) else ''
            if self.all_images and not txt:
                txt = 'IMG'
            if not self.no_images and (len(txt) > 1 or self.all_images):
                tsp = ' ' if (len(src) < 40) else ''
                return '%s![ %s ]( %s%s)' % (br, txt, src, tsp)
            elif txt:
                return txt
            else:
                return ''

        if t == 'a':
            href = adict.get('href', '')
            text = b.strip()
            br = '\n' if (len(href+text) > wrap or ' ' not in text) else ''
            if href in ('', '#'):
                return text + ' '
            elif text and (text != href):
                tsp = ' ' if (len(href) < wrap-3) else ''
                return '%s[ %s ]( %s%s) ' % (br, text, href, tsp)
            elif text or self.all_links:
                return '%s<%s> ' % (br, href)
            else:
                return ''

        if t in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            hashes = '#' * int(t[1])
            text = b.strip()
            return ('\n%s %s\n\n' % (hashes, text)) if text else ''

        if t in ('p', ):
            return '\n' + self._wrap_text(b.strip(), wrap=wrap) + '\n\n'

        if t == 'pre':
            return '\n```\n%s\n```\n' % _strips(b)

        if t == 'blockquote':
            contents = '\n> '.join(_strips(b).splitlines())
            contents = contents.replace('\n> \n> \n', '\n>\n')
            return '\n> %s\n' % contents

        if t == 'li':
            contents = b.rstrip()
            if '* ' not in contents:
                contents = self._wrap_text(contents.strip(), wrap=wrap)
            if '\n' in contents:
                contents = '\n  '.join(contents.splitlines())
            return '\n* ' + contents

        if t in ('ul', 'ol'):
            ind = '\n   ' if (wrap == self.wrap - 3) else '\n '
            return ind + ind.join(_strips(b).splitlines()) + '\n\n'

        if t in ('div',):
            if ' * ' in b:
                return _strips(b) + '\n\n'
            else:
                return self._wrap_text(_strips(b), wrap=wrap) + '\n\n'

        if False:  # FIXME
            if t in ('th', 'td', 'tr', 'tbody', 'table'):
                return '<%s>%s</%s>' % (t, b, t)

        else:
            if t in ('th', ):
                text = b.strip()
                return ('%s ' % text) if text else ''

            if t in ('div', 'tr', 'tbody', 'table'):
                if ' * ' in b:
                    return _strips(b) + '\n\n'
                else:
                    return self._wrap_text(_strips(b), wrap=wrap) + '\n\n'

            if t in ('td', ):
                return _strips(b) + ' '

        return self._wrap_text(_strips(b), wrap=wrap)


def html_to_markdown(html, **kwargs):
    from .css import CSSCleaner
    cleaner = HTMLToTextCleaner(html, **kwargs, css_cleaner=CSSCleaner())
    return re.sub(r'\n[\s]+\n', '\n\n', cleaner.close(), flags=re.DOTALL)



def clean_email_html(metadata, email, part,
        id_signer=None,
        inline_images=False,
        remote_images=False,
        target_blank=False):

    if metadata.idx and (id_signer is not None):
        url_prefix = '/cli/show/%s?part=' % id_signer('id:%s' % metadata.idx)
    else:
        url_prefix = 'cid:'

    def _find_by_cid(cid):
        for i, p in enumerate(email['_PARTS']):
            if p.get('content-id') == cid:
                return (i+1), p
        return None, None

    def a_fixup(cleaner, tag, attrs, data):
        if target_blank:
            # FIXME: Exempt links that are anchors within this document?
            # FIXME: Forbid relative links!
            return tag, cleaner._aa(attrs, 'target', '_blank'), data
        else:
            return tag, attrs, data

    def img_fixup(cleaner, tag, attrs, data):
        dropping = []
        for i, (a, v) in enumerate(attrs):
            if v and (a == 'data-m-src'):
                if v.startswith('cid:'):
                    idx, part = _find_by_cid(v[4:].strip())
                    part_id = None
                    if idx and part:
                        part_id = magic_part_id(idx, part)
                        if not part_id:
                            cleaner.saw_danger += 1
                    if part_id:
                        an = 'src' if inline_images else 'data-m-src'
                        attrs[i] = (an, url_prefix + part_id)
                    else:
                        dropping.append(i)
                elif remote_images and v.startswith('http'):
                    pass  # FIXME: Update URL to use our proxy

        for idx in reversed(dropping):
            cleaner.dropped_attrs.append((tag, attrs[idx][0], attrs[idx][1]))
            attrs.pop(idx)

        return tag, attrs, data

        # FIXME; Do we also want to fixup other URLs?
        #        3rd party image loading is blocked by our CSP,
        #        ... so we need a proxy if they are to work at all.
        #        Attempt to block tracking images?
        #        Load content over Tor?
        #        Redirect clicks through some sort of security checker?

    from moggie.security.css import CSSCleaner
    return HTMLCleaner(part['_TEXT'],
        callbacks={
            'img': img_fixup,
            'a': a_fixup
        },
        css_cleaner=CSSCleaner()).clean()


if __name__ == '__main__':
    import sys

    if sys.argv[1:] == ['-']:
        from .css import CSSCleaner
        cleaner = HTMLCleaner(sys.stdin.read(), css_cleaner=CSSCleaner())
        print(cleaner.clean())
    elif sys.argv[1:] == ['--to-markdown']:
        print(html_to_markdown(sys.stdin.read(), wrap=72))
    else:
        input_data = """\
<!DOCTYPE html>
<html><head>
  <title>Hello world</title>
  <script>Very dangerous content</script>
</head><body>
  <p><h1>Hello <b>world < hello universe<p></h1>Para two<hr>
  <a href="http://spamsite/">https://<b>www.google.com</b>/</a>
  <a href="http://google.com.spamsite/">https://www.google.com/</a>
  <a href="https://www.google.com/">google.com</a>
  <a href="https://www.google.com/things/and/stuff">www.google.com</a>
  <a href="https://www.google.com@evil.com/">click me</a>
  <a href="javascript:alert('evil')">hooray</a>
  <ul onclick="evil javascript;">
    <li>One
    <li>Two
    <li><ol><li>Three<li>Four</ol>
  </ul>
  <table>
    <tr><td>Hello<td>Lame<td>Table
  </table>
</body>"""

        def mk_kwe(kw):
            def kwe(tag, attrs, text):
                nonlocal kw
                if tag not in ('script', 'style'):
                     kw |= set(w.strip().lower()
                         for w in text.split(' ') if len(w) >= 3)
            return kwe

        keywords = set()
        cleaner = HTMLCleaner(input_data, callbacks={'DATA': mk_kwe(keywords)})
        cleaned = cleaner.close()
        cleaner.keywords |= keywords

        assert('DOCTYPE' not in cleaned)
        assert('<html'   not in cleaned)
        assert('<title'  not in cleaned)
        assert('<p></h1' not in cleaned)
        assert('</hr>'   not in cleaned)
        assert('onclick' not in cleaned)
        assert('javascr' not in cleaned)

        assert('<h1>Hello'        in cleaned)
        assert('world &lt; hello' in cleaned)

        assert('href'  not in cleaner.keywords)
        assert('body'  not in cleaner.keywords)

        assert('lame'           in cleaner.keywords)
        assert('hello'          in cleaner.keywords)
        assert('html:spooky'    in cleaner.keywords)
        assert('html:code-dfal' in cleaner.keywords)

        print(cleaned + cleaner.report())
