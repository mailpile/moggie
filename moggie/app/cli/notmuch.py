# These are CLI commands which aim to behave as similarly to notmuch as
# possible. Because why not? Compatibility is nice.
#
# FIXME: Most of the complex logic in here should probably be moved to the
#        back-end, so we can expose the same API via the web.
#
# TODO: Look into tabular
#
# These are the commands used by the dodo mail client:
#    notmuch new
#    notmuch tag +replied -- id:<MSGID>
#    notmuch search --format=json <QUERY>
#    notmuch tag <EXPRESSION> -- id:<MSGID>
#    notmuch search --output=tags *
#    notmuch count --output=threads -- tag:<TAG>
#    notmuch count --output=threads -- tag:<TAG> AND tag:unread
#    notmuch show --format=json --include-html <THREADID>
#
#NOTES:
#   - For 3rd party search integration, we need to include enough details
#     in our results for a 3rd party app to construct working URLs to
#     individual messages. We would rather 3rd parties not hard-code our
#     URL structure, so we should provide these details.
#   - These URLs used to access individual e-mails should be tied to the
#     Access object, but without leaking the token itself. So some sort
#     of simple signature which lets us revoke the URLs along with the
#     access object.
#
import base64
import copy
import datetime
import json
import logging
import io
import os
import re
import sys
import time

from .command import Nonsense, CLICommand, AccessConfig
from ...config import AppConfig
from ...email.metadata import Metadata
from ...jmap.requests import RequestSearch, RequestMailbox, RequestEmail
from ...security.html import HTMLCleaner
from ...security.css import CSSCleaner
from ...storage.exporters.mbox import MboxExporter
from ...storage.exporters.maildir import MaildirExporter, EmlExporter
from ...util.mailpile import tag_unquote
from ...util.dumbcode import dumb_decode


class CommandSearch(CLICommand):
    """# moggie search [options] <search terms ...>

    Search for emails or threads matching the given search terms. Search
    terms are exact matches, unless the wildcard (*) is used. Examples:

        moggie search bjarni                 # Exact match
        moggie search bjarn*                 # Will match bjarni or bjarna

        moggie search in:inbox tag:unread    # Both in the inbox and unread
        moggie search in:inbox -tag:unread   # In the inbox, not unread
        moggie search in:inbox +tag:unread   # In the inbox, or unread
        moggie search bjarni --format=json   # JSON for further processing...

        moggie search dates:2022-08 --format=mbox > August2022.mbx  # Export!

    Options:                                   (defaults are marked with a *)

        --context=<ctx>  Choose which context to search within.
        --format=<fmt>   Result format: text*, text0, json, zip, maildir, mbox
        --output=<data>  Result output: summary*, threads, messages, files,
                                        tags, emails, thread_emails.
        --offset=<N>     Skip the first N results
        --limit=<N>      Output at most N results
        --sort=<N>       Either newest-first (the default) or oldest-first.

    The search command can emit various types of results in various formats.
    Some constraints and special cases:

       * The default output is `summary`, unless something else is implied
         by the format. The default format is `text`.
       * The only valid outputs for zip, maildir and mbox are emails and
         thread_emails.
       * The maildir format actually generates a gzipped tar archive, which
         contains the maildir.
       * The headers of messages contained in mbox and zip results will be
         modified to include Moggie metadata (tags, read/unread, etc.).
       * Searching for `*` returns all known mail.
       * Searching for `mailbox:/path/to/mailbox` can be used to extract
         information from a mailbox directly.
       * File listings may not encode to Unicode correctly, since *nix
         filenames are in fact binary data, not UTF-8. This means JSON
         formatting with `--output=files` may fail in some cases. Use
         `--format=text0` for the most reliable results.

    Where moggie and notmuch options overlap (see `man notmuch`), an attempt
    has been made to ensure compatibility. However, note that Moggie file
    paths have extra data appended (offets within a mbox, etc). Moggie's
    search syntax also differs from that of notmuch in important ways.
    """
    NAME = 'search'
    ROLES = AccessConfig.GRANT_READ
    WEBSOCKET = False
    WEB_EXPOSE = True
    HTML_DEFAULT_LIMIT = 25
    HTML_COLUMNS = ['count', 'thread', 'address', 'name', 'authors',
                    'tags', 'subject', 'date_relative']
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--q=':              [],
        '--username=':       [None],
        '--password=':       [None],
        '--zip-password=':   [None],
        # These are notmuch options which we implement
        '--format=':         ['text'],
        '--output=':         ['default'],
        '--offset=':         ['0'],
        '--limit=':          [''],
        '--entire-thread=':  [],
        # These are notmuch options which we currently ignore
        '--sort=':           ['newest-first'],
        '--format-version=': [''],
        '--exclude=':        ['true'],
        '--duplicate=':      ['']}

    def __init__(self, *args, **kwargs):
        self.displayed = {}
        self.default_output = 'summary'
        self.fake_tid = int(time.time() * 1000)
        self.raw_results = None
        self.exporter = None
        super().__init__(*args, **kwargs)

    def configure(self, args):
        self.batch = 10000

        # Allow both --q=.. and unmarked query terms. The --q=
        # option is mostly for use with the web-CLI.
        terms = self.strip_options(args)
        terms.extend(self.options['--q='])
        self.terms = ' '.join(terms)

        fmt = self.options['--format='][-1]
        if fmt in ('json', 'jhtml'):
            self.mimetype = 'application/json'
        elif fmt == 'html':
            self.mimetype = 'text/html; charset=utf-8'
        elif fmt == 'mbox':
            self.mimetype = 'application/mbox'
        elif fmt == 'zip':
            self.mimetype = 'application/mbox'
        elif fmt == 'maildir':
            self.mimetype = 'application/x-tgz'

        if fmt in ('html', 'jhtml') and not self.options['--limit='][-1]:
            self.options['--limit='].append(self.HTML_DEFAULT_LIMIT)

        if self.options['--format='][-1] in ('maildir', 'zip', 'mbox'):
            self.default_output = 'emails'

        if (self.options['--zip-password='][-1] and
               self.options['--format='][-1] not in ('zip',)):
            raise Nonsense('Encryption is only supported with --format=zip')

        self.preferences = self.cfg.get_preferences(context=self.context)
        return []

    async def as_metadata(self, md):
        if md is not None:
            yield ('%s', Metadata(*md).parsed())

    def _as_thread(self, result):
        if 'thread' in result:
            return result
        fake_tid = result[Metadata.OFS_IDX] or self.fake_tid
        self.fake_tid += 1
        return {
            'thread': fake_tid,
            'messages': [result],
            'hits': [fake_tid]}

    async def as_threads(self, thread):
        if thread is not None:
            yield ('%s', 'thread:%8.8d' % self._as_thread(thread)['thread'])

    def _relative_date(self, ts):
        dt = datetime.datetime.fromtimestamp(ts)
        if (time.time() - ts) < (23 * 3600):
            return '%2.2d:%2.2d' % (dt.hour, dt.minute)
        else:
            return '%4.4d-%2.2d-%2.2d' % (dt.year, dt.month, dt.day)

    async def as_summary(self, thread):
        if thread is None:
            return
        thread = self._as_thread(thread)
        if thread['hits']:
            tid = thread['thread']
            msgs = dict((i[1] or tid, Metadata(*i).parsed())
                for i in thread['messages'])

            top = msgs[tid]
            md = msgs[thread['hits'][0]]

            ts = min(msgs[i]['ts'] for i in thread['hits'])
            fc = sum(len(m['ptrs']) for m in msgs.values())

            tags = []
            for msg in msgs.values():
                tags.extend(msg.get('tags', []))
            tags = [t.split(':')[-1] for t in set(tags)]

            authors = ', '.join(list(set(
                (m['from']['fn'] or m['from']['address'])
                for m in msgs.values() if 'from' in m)))
            info = {
                'thread': '%8.8d' % tid,
                'timestamp': ts,
                'date_relative': self._relative_date(ts),
                'matched': len(thread['hits']),
                'total': len(msgs),
                'files': fc,
                'authors': authors,
                'subject': top.get('subject', md.get('subject', '(no subject)')),
                'query': [self.sign_id('id:%s' % ','.join('%d' % mid for mid in msgs))] + [None],
                'tags': tags}
            info['_url_thread'] = '/cli/show/%s' % info['query'][0]
            info['_tag_list'] = ' (%s)' % (' '.join(tags)) if tags else ''
            info['_file_count'] = '(%d)' % fc if (fc > len(msgs)) else ''
            info['_id'] = (
                ('id:%12.12d' % tid) if (len(msgs) == 1) else
                ('thread:' + info['thread']))
            yield (
                '%(_id)s %(date_relative)s'
                ' [%(matched)s/%(total)s%(_file_count)s]'
                ' %(authors)s;'
                ' %(subject)s%(_tag_list)s',
                info)

    async def as_messages(self, md):
        if md is not None:
            md = Metadata(*md)
            yield ('%s', self.sign_id('id:%8.8d' % md.idx))

    async def as_tags(self, tag_info):
        if tag_info is not None:
            yield ('%s', tag_info[0])

    async def as_tag_info(self, tag_info):
        if tag_info is not None:
            yield ('%(tag)s\t%(info)s', {
                'tag': tag_info[0],
                'info': tag_info[1][0],
                'hits': tag_info[1][1]})

    async def as_files(self, md):
        # FIXME: File paths are BINARY BLOBS. We need to output them as
        #        such, if possible. Especially in text mode!
        if md is not None:
            md = Metadata(*md)
            for p in md.pointers:
                fn = dumb_decode(p.ptr_path)
                try:
                    fn_str = str(fn, 'utf-8')
                except UnicodeDecodeError:
                    fn_str = str(fn, 'latin-1')
                yield (fn, fn_str)

    FIX_MIMETYPES = {
        'image/jpg': 'image/jpeg',
        'multipart/alternative': 'text/plain',
        'multipart/related': 'text/plain',
        'multipart/mixed': 'text/plain'}
    EVIL_EXTENSIONS = set(['exe', 'dll', 'scr', 'com'])  # FIXME
    RISKY_MT_CHARS = re.compile(r'[^a-z0-9_\./-]')
    RISKY_FN_CHARS = re.compile(r'[^a-zA-Z0-9_\.-]')

    def _filename(self, part):
        filename = ''
        disp = part.get('content-disposition')
        ctype = part.get('content-type')
        for which, attr in ((disp, 'filename'), (ctype, 'name')):
            if which and attr in which[1]:
                filename = self.RISKY_FN_CHARS.sub('_', which[1][attr])
                if filename:
                    return filename
        return None

    def _magic_part_id(self, idx, part):
        mimetype = part.get('content-type', ['application/octet-stream'])[0]
        mimetype = self.RISKY_MT_CHARS.sub('_', mimetype.lower())
        mimetype = self.FIX_MIMETYPES.get(mimetype, mimetype)

        filename = self._filename(part) or ''
        if filename:
            ext = filename.split('.')[-1]
            # FIXME: Fix mime-type based on extension? Check for mismatch?
            if ('..' in filename) or (ext in self.EVIL_EXTENSIONS):
                return None

        return 'part-%d-%s%s%s' % (
            idx, mimetype.replace('/', '-'), '/' if filename else '', filename)

    def _fix_html(self, metadata, msg, part):
        show_html_ii = (self.preferences['display_html_inline_images'] == 'yes')
        show_html_ri = (self.preferences['display_html_remote_images'] == 'yes')
        target_blank = (self.preferences['display_html_target_blank'] == 'yes')

        email = msg['email']
        if metadata.idx:
            signed_id = self.sign_id('id:%s' % metadata.idx)
            url_prefix = '/cli/show/%s?part=' % signed_id
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
                            part_id = self._magic_part_id(idx, part)
                            if not part_id:
                                cleaner.saw_danger += 1
                        if part_id:
                            an = 'src' if show_html_ii else 'data-m-src'
                            attrs[i] = (an, url_prefix + part_id)
                        else:
                            dropping.append(i)
                    elif show_html_ri and v.startswith('http'):
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

        return HTMLCleaner(part['_TEXT'],
            callbacks={
                'img': img_fixup,
                'a': a_fixup
            },
            css_cleaner=CSSCleaner()).clean()

    async def as_emails(self, thread):
        def _textify(r, prefer, esc, part_fmt, msg_fmt, hdr_fmt='%(h)s: %(v)s'):
            headers = '\n'.join(
                hdr_fmt % {'hc': h.lower(), 'h': h, 'v': esc(r['headers'][h])}
                for h in ('Date', 'To', 'Cc', 'From', 'Reply-To', 'Subject')
                if h in r['headers'])

            def _classify(parent_ct, parts):
                types = []
                for i, p in enumerate(parts):
                    ct = p.get('content-type', '')
                    p['_pref'] = ''
                    if ('text/' in ct) and p.get('content'):
                        types.append(ct)
                        p['class'] = 'part mPartInline'
                    elif 'multipart/' in ct:
                        types.append(None)
                        p['class'] = 'part mPartStructure'
                    else:
                        types.append(None)
                        p['class'] = 'part mPartAttachment'
                if (parent_ct == 'multipart/alternative') and (len(parts) > 1):
                    try:
                        preferred = types.index(prefer)
                    except ValueError:
                        preferred = [(1 if t else 0) for t in types].index(1)
                    for i, p in enumerate(parts):
                        if i == preferred:
                            p['class'] += ' mShow'
                            p['_pref'] = ' (preferred)'
                        else:
                            p['class'] += ' mHide'
                return parts

            url_prefix = '/cli/show/id:%s' % r['id']
            def _part(p):
                p['_ct'] = ct = esc(p.get('content-type', 'text/plain'))
                p['_fn'] = ''
                p['_url'] = '%s?part=%s' % (url_prefix, p['magic-id'])
                if 'filename' in p:
                    p['_fn'] = esc('Filename: %s, ' % p['filename'])
                if 'content' in p:
                    if isinstance(p['content'], list):
                        p['content'] = '\n'.join(
                            _part(sp) for sp in _classify(ct, p['content']))
                else:
                    p['content'] = (p['_fn'] or 'Non-text part: ') + p['_ct']
                return (part_fmt % p)

            return (msg_fmt % {
                'd': r['_depth'],
                'i': r['id'],
                'm': 1 if r['match'] else 0,
                'e': 1 if r['excluded'] else 0,
                'f': esc(r['filename'][0] if r['filename'] else ''),
                'a': esc(r['headers'].get('From', '(unknown)')),
                'r': r['date_relative'],
                't': ' '.join(r['tags']),
                'h': headers,
                'b': '\n'.join(_part(sp) for sp in _classify('', r['body']))})

        def _as_text(r):
            return _textify(r, 'text/plain', lambda t: t,
                """\
\x0cpart{ ID: %(id)s, %(_fn)sContent-type: %(_ct)s%(_pref)s
%(content)s
\x0cpart}""",
                """\
\x0cmessage{ id:%(i)s depth:%(d)d match:%(m)d excluded:%(e)s filename:%(f)s
\x0cheader{
%(a)s (%(r)s) (%(t)s)
%(h)s
\x0cheader}
\x0cbody{
%(b)s
\x0cbody}
\x0cmessage}""")
        def _as_html(r):
            def _html_quote(t):
                return (t
                    .replace('&', '&amp;')
                    .replace('<', '&lt;')
                    .replace('>', '&gt;'))
            return _textify(r, 'text/html', _html_quote,
                """
    <div class="%(class)s" data-part-id="%(id)s" data-mimetype="%(_ct)s">
      <a class="mDownload" href="%(_url)s">Download</a>
      %(content)s
    </div>
""",
                """\
<a name="id_%(i)s"></a>
<div class=email data-id="%(i)s" data-match="%(m)d" data-depth="%(d)d" data-excluded="%(e)s">
  <div class="email-summary">
    <span class="">%(a)s</span>
    <span class="">(%(r)s)</span>
    <span class="">(%(t)s)</span>
  </div>
  <table class="email-header">
%(h)s
  </table>
  <div class="email-body">%(b)s
  </div>
</div>
""",
                """\
    <tr class="email-%(hc)s"><th>%(h)s:</th><td>%(v)s</td></tr>""")

        if thread is not None:
            fmt = self.options['--format='][-1]
            part = int((self.options.get('--part=') or [0])[-1])
            raw = (fmt in ('mbox', 'maildir', 'raw', 'zip'))
            want_body = raw or (self.options.get('--body=', [0])[-1] != 'false')
            want_html = self.options.get('--include-html')
            shown_types = ('text/plain', 'text/html') if want_html else ('text/plain',)

            thread = self._as_thread(thread)
            for md in thread['messages']:
                md = Metadata(*md)
                if want_body:
                    query = RequestEmail(
                        metadata=md,
                        text=(not raw or part),
                        data=(True if part else False),
                        parts=([part-1] if part else None),
                        full_raw=(raw and not part))
                    query['context'] = self.context
                    msg = await self.worker.async_jmap(self.access, query)
                else:
                    msg = {'email': md.parsed()}

                if not msg or not msg.get('email'):
                    pass

                elif part:
                    _part = msg['email']['_PARTS'][part-1]
                    yield (_part.get('_TEXT'),
                        {'_metadata': md,
                         '_mimetype': _part['content-type'][0],
                         '_data': _part.get('_DATA')})

                elif raw and msg['email'].get('_RAW'):
                    yield ('',
                        {'_metadata': md, '_data': msg['email'].get('_RAW')})

                else:
                    headers = {}
                    for hdr in ('Subject', 'From', 'To', 'Cc', 'Date'):
                        val = msg['email'].get(hdr.lower())
                        if isinstance(val, str):
                            headers[hdr] = val
                        elif isinstance(val, dict):
                            headers[hdr] = ('%s <%s>' % (val['fn'], val['address'])).strip()
                        elif isinstance(val, list) and val:
                            if isinstance(val[0], str):
                                headers[hdr] = ', '.join(val)
                            else:
                                headers[hdr] = ', '.join(
                                    ('%s <%s>' % (v['fn'], v['address'])).strip()
                                    for v in val)
                    body = []
                    if '_PARTS' in msg['email']:
                        partstack = [body]
                        depth = 0
                        for i, _part in enumerate(msg['email']['_PARTS']):
                            info = {
                                'id': i+1,
                                'content-type': _part.get('content-type', ['text/plain'])[0]}
                            part_id = self._magic_part_id(i+1, _part)
                            if part_id:
                                info['magic-id'] = part_id

                            filename = self._filename(_part)
                            if filename:
                                info['filename'] = filename

                            disp = _part.get('content-disposition')
                            if isinstance(disp, list):
                                info['content-disposition'] = disp[0]
                                cte = _part.get('content-transfer-encoding')
                                if cte:
                                    info['content-transfer-encoding'] = cte
                                info['content-length'] = (_part['_BYTES'][2] - _part['_BYTES'][1])
                            if 'content-id' in _part:
                                info['content-id'] = _part['content-id']
                            while _part['_DEPTH'] < depth:
                                partstack.pop(-1)
                                depth -= 1
                            if info['content-type'] != 'text/x-mime-postamble':
                                partstack[-1].append(info)
                            if '_TEXT' in _part and info['content-type'] in shown_types:
                                if 'html' in info['content-type']:
                                    info['content'] = self._fix_html(
                                        md, msg, _part)
                                else:
                                    info['content'] = _part['_TEXT']
                            elif info['content-type'].startswith('multipart/'):
                                info['content'] = []
                                partstack.append(info['content'])
                                depth += 1

                    if fmt in ('html', 'jhtml'):
                        func = _as_html
                    else:
                        func = _as_text
                    yield (func, {
                        'id': self.sign_id('id:%s' % md.idx)[3:],
                        'match': md.idx in thread['hits'],
                        'excluded': False,
                        'timestamp': md.timestamp,
                        'filename': [p async for t, p in self.as_files(md)],
                        'date_relative': self._relative_date(md.timestamp),
                        'tags': [t.split(':')[-1] for t in (md.more.get('tags') or [])],
                        'body': body,
                        'crypto': {},
                        'headers': headers,
                        '_id': md.idx,
                        '_thread_id': md.thread_id,
                        '_parent_id': md.parent_id,
                        '_depth': 0,
                        '_fn': 'FIXME',
                        '_header': 'FIXME',
                        '_metadata': md,
                        '_parsed': msg})

    async def emit_result_raw(self, result, first=False, last=False):
        if result is not None:
            raw = result[1] and result[1].get('_data')
            data = base64.b64decode(raw) if raw else result[0] or ''
            self.write_reply(data)

    async def emit_result_text(self, result, first=False, last=False):
        if result is not None:
            if isinstance(result[0], bytes):
                self.write_reply(result[0] + b'\n')
            elif isinstance(result[0], str):
                self.print(result[0] % result[1])
            else:
                self.print(result[0](result[1]))

    async def emit_result_text0(self, result, first=False, last=False):
        if result is not None:
            if isinstance(result[0], bytes):
                self.write_reply(result[0] + b'\0')
            elif isinstance(result[0], str):
                self.write_reply((result[0] % result[1]) + '\0')
            else:
                self.write_reply(result[0](result[1]) + '\0')

    def _json_sanitize(self, result):
        if isinstance(result[1], dict):
            result1, keys = copy.copy(result[1]), result[1].keys()
            for k in keys:
                if k[:1] == '_':
                    del result1[k]
            result = (None, result1)
        return result

    async def emit_result_json(self, result, first=False, last=False):
        if result is None:
            return
        result = self._json_sanitize(result)
        if first:
            self.print('[', nl='')
        if result:
            self.print_json(result[1], nl='')
        self.print(']' if last else ',')

    async def emit_result_sexp(self, result, first=False, last=False):
        if result is None:
            return
        result = self._json_sanitize(result)
        if first:
            self.print('(', nl='')
        if result:
            self.print_sexp(result[1], nl='')
        self.print(')' if last else ' ')

    async def emit_result_jhtml(self, result, first=False, last=False):
        if result is None:
            return
        tabular = isinstance(result[0], str)
        if tabular:
            pre, post = '<table class=results>', '</table>'
        else:
            pre, post = '<div class=results>', '</div>'
        if first:
            self.print('{"state": %s, "html": "%s'
                % (json.dumps(self.webui_state), pre), nl='')
        if tabular:
            self.print(
                self.format_html_tr(result[1], columns=self.HTML_COLUMNS)
                .replace('"', '\\"'), nl='')
        else:
            self.print(json.dumps(result[0](result[1]))[1:-1], nl='')
        if last:
            self.print('%s"}' % post)

    async def emit_result_html(self, result, first=False, last=False):
        if result is None:
            return
        tabular = isinstance(result[0], str)
        if tabular:
            pre, post = '<table class=results>', '</table>'
        else:
            pre, post = '<div class=results>', '</div>'
        if first:
            self.print_html_start(pre)
        if tabular:
            self.print_html_tr(result[1], columns=self.HTML_COLUMNS)
        else:
            self.print(result[0](result[1]))
        if last:
            self.print_html_end(post)

    def _get_exporter(self, cls):
        if self.exporter is None:
            password = self.options['--zip-password='][-1]
            class _wwrap:
                def write(ws, data):
                    self.write_reply(data)
                    return len(data)
                def flush(ws):
                    pass
                def close(ws):
                    pass
            if password:
                self.exporter = cls(_wwrap(), password=bytes(password, 'utf-8'))
                if not self.exporter.can_encrypt():
                    raise Nonsense('Encryption is unavailable')
            else:
                self.exporter = cls(_wwrap())
        return self.exporter

    def _export(self, exporter, result, first, last):
        if result is not None:
            metadata = result[1]['_metadata']
            raw_email = base64.b64decode(result[1]['_data'])
            exporter.export(metadata, raw_email)
        if last:
            exporter.close()

    async def emit_result_mbox(self, result, first=False, last=False):
        exporter = self._get_exporter(MboxExporter)
        return self._export(exporter, result, first, last)

    async def emit_result_zip(self, result, first=False, last=False):
        exporter = self._get_exporter(EmlExporter)
        return self._export(exporter, result, first, last)

    async def emit_result_maildir(self, result, first=False, last=False):
        exporter = self._get_exporter(MaildirExporter)
        return self._export(exporter, result, first, last)

    def get_output(self):
        output = (self.options['--output='] or ['default'])[-1]
        if output == 'default':
            output = self.default_output
        return output

    def get_formatter(self):
        output = self.get_output()
        if output == 'summary':
            return self.as_summary
        elif output == 'threads':
            return self.as_threads
        elif output == 'messages':
            return self.as_messages
        elif output == 'tags':
            return self.as_tags
        elif output == 'tag_info':
            return self.as_tag_info
        elif output == 'files':
            self.write_error = lambda e: None
            return self.as_files
        elif output == 'metadata':
            self.write_error = lambda e: None
            return self.as_metadata
        elif output == 'emails':
            self.write_error = lambda e: None
            return self.as_emails
        raise Nonsense('Unknown output format: %s' % output)

    def get_emitter(self):
        fmt = self.options['--format='][-1]
        if fmt == 'json':
            return self.emit_result_json
        elif fmt == 'jhtml':
            return self.emit_result_jhtml
        elif fmt == 'html':
            return self.emit_result_html
        elif fmt == 'text0':
            return self.emit_result_text0
        elif fmt in 'text':
            return self.emit_result_text
        elif fmt == 'sexp':
            return self.emit_result_sexp
        elif fmt in 'raw':
            return self.emit_result_raw
        elif fmt == 'mbox':
            return self.emit_result_mbox
        elif fmt == 'maildir':
            return self.emit_result_maildir
        elif fmt == 'zip':
            return self.emit_result_zip
        raise Nonsense('Unknown output format: %s' % fmt)

    def get_query(self):
        fmt = self.options['--format='][-1]
        output = self.get_output()

        if self.terms.startswith('mailbox:'):
            valid_outputs = ('default', 'threads', 'summary', 'metadata',
                             'files', 'emails')
            if output not in valid_outputs:
                raise Nonsense('Need --output=X, with X one of: %s'
                    % ', '.join(valid_outputs))
            query = RequestMailbox(
                context=self.context,
                username=self.options['--username='][-1],
                password=self.options['--password='][-1],
                mailbox=self.terms[8:])
        else:
            query = RequestSearch(context=self.context, terms=self.terms)

        if self.options.get('--offset=', [None])[-1]:
            query['skip'] = int(self.options['--offset='][-1])
        else:
            query['skip'] = 0

        if output == 'summary':
            query['threads'] = True
            query['only_ids'] = False
            self.batch = 2000
        elif output == 'threads':
            query['threads'] = True
            query['only_ids'] = True
        elif output == 'emails':
            entire = (self.options.get('--entire-thread=') or ['false'])[-1]
            if entire != 'false':
                query['threads'] = True
        elif output in ('tags', 'tag_info'):
            query['uncooked'] = True
            if ((query['skip'] or self.options.get('--limit=', [None])[-1])
                    and (fmt not in ('html', 'jhtml'))):
                raise Nonsense('Offset and limit do not apply to tag searches')

        return query

    async def run(self):
        query = self.get_query()  # Note: May alter self.default_output

        formatter = self.get_formatter()
        emitter = self.get_emitter()
        self.sign_id = self.make_signer()

        limit = None
        if self.options.get('--limit=', [None])[-1]:
            limit = int(self.options['--limit='][-1])

        prev = None
        first = True
        async for result in self.results(query, limit, formatter):
            if prev is not None:
                await emitter(prev, first=first)
                first = False
            prev = result
        await emitter(prev, first=first, last=True)

    def make_signer(self):
        if (self.access is not True) and self.access._live_token:
            access_id = self.access.config_key[len(AppConfig.ACCESS_PREFIX):]
            context_id = self.context[len(AppConfig.CONTEXT_PREFIX):]
            token = self.access._live_token
            def id_signer(_id):
                _id = '%s.%s.%s' % (_id, access_id, context_id)
                sig = self.access.make_signature(_id, token=token)
                return '%s.%s' % (_id, sig)
            return id_signer
        else:
            return lambda _id: _id

    async def perform_query(self, query, batch, limit):
        query['limit'] = min(batch, limit or batch)
        msg = await self.worker.async_jmap(self.access, query)
        if 'emails' not in msg and 'results' not in msg:
            raise Nonsense('Search failed. Is the app locked?')

        if not self.raw_results:
            self.raw_results = msg['results']
            self.webui_state['details'] = {}
            self.webui_state['preferences'] = self.preferences
            for k in self.raw_results:
                if k not in ('hits', 'tags'):
                    self.webui_state['details'][k] = self.raw_results[k]

        output = self.get_output()
        if output in ('tags', 'tag_info'):
            return (msg.get('results', {}).get('tags') or {}).items()
        else:
            return msg.get('emails') or []

    async def results(self, query, limit, formatter):
        batch = self.batch // 10
        output = self.get_output()
        while limit is None or limit > 0:
            results = await self.perform_query(query, batch, limit)
            batch = min(self.batch, int(batch * 1.2))
            count = len(results)
            if limit is not None:
                limit -= count

            for r in results:
                async for fd in formatter(r):
                    yield fd

            query['skip'] += count
            if ((count < query['limit'])
                    or (not count)
                    or (output in ('tags', 'tag_info'))):
                break

        async for fd in formatter(None):
            yield fd


class CommandAddress(CommandSearch):
    """# moggie address [options] <search terms ...>

    Search for emails or threads matching the given search terms and display
    addresses related to them (senders, recipients or both). Examples:

        moggie address to:bre dates:2022-09
        moggie address --output=recipients from:bre dates:2022-09

    Options:                                   (defaults are marked with a *)

        --format=<F>       One of text*, text0, json or sexp
        --output=<O>       One or more of sender*, recipients, address, count
        --deduplicate=<D>  One of no, mailbox*, address

    When choosing output formats, multiple options can be specified at once.
    When `sender` is requested, the output will include all senders. The
    output from `recipients` includes the messages in To: and Cc: headers.
    Requesting `--output=address` will omit the names from e-mail addresses,
    and `--output=count` will include a count of how often each address was
    seen.

    See also `moggie help search` and `moggie help how-to-search` for details
    about how to search for mail. This command should be compatible with its
    `notmuch` counterpart, so the man-page for `notmuch address` may also
    provide useful insights.
    """
    NAME = 'address'
    ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--q=':              [],
        # These are notmuch options which we implement
        '--format=':         ['text'],
        '--output=':         [],
        '--deduplicate=':    ['mailbox'],
        # These are notmuch options which we currently ignore
        '--sort=':           ['newest-first'],
        '--format-version=': [''],
        '--exclude=':        ['true']}

    def __init__(self, *args, **kwargs):
        self.address_only = False
        self.result_cache = {}
        self.counts = False
        super().__init__(*args, **kwargs)

    def configure(self, args):
        args = super().configure(args)
        if not self.options['--output=']:
            self.options['--output='].append('sender')
        if (self.options['--deduplicate='][-1] == 'no'
                and 'count' in self.options['--output=']):
            raise Nonsense('Counting requires deduplication')
        return args

    def is_new(self, addr, output):
        dedup = self.options['--deduplicate='][-1]
        if dedup == 'no':
            return True
        if dedup == 'address':
            d = addr.address
        else:
            d = str(addr)
        if d in self.displayed:
            self.displayed[d] += 1
            return False
        else:
            self.displayed[d] = 1
            if self.counts:
                self.result_cache[d] = output
            return True

    async def emit_sender(self, md):
        addr = Metadata(*md).parsed().get('from')
        result = {
            'address': addr.address,
            'name': addr.fn,
            'name-addr': '%s <%s>' % (addr.fn, addr.address)}
        if addr and self.is_new(addr, result):
            yield (self.fmt, result)

    async def emit_recipients(self, md):
        mdp = Metadata(*md).parsed()
        for hdr in ('to', 'cc', 'bcc'):
            for addr in mdp.get(hdr, []):
                result = {
                    'address': addr.address,
                    'name': addr.fn,
                    'name-addr': (
                        ('%s <%s>' % (addr.fn, addr.address)) if addr.fn else
                        ('<%s>' % (addr.address)))}
                if self.is_new(addr, result):
                    yield (self.fmt, result)

    def get_formatter(self):
        fmt = self.options['--format='][-1]
        outs = self.options['--output=']
        formatters = []
        for out in outs:
            if out == 'sender':
                formatters.append(self.emit_sender)
            elif out == 'recipients':
                formatters.append(self.emit_recipients)
            elif out in ('address', 'addresses'):
                self.address_only = True
            elif out == 'count':
                self.counts = True
            else:
                raise Nonsense('Unknown output type: %s' % out)
        if not formatters:
            formatters.append(self.emit_sender)

        self.fmt = '%(address)s' if self.address_only else '%(name-addr)s'
        async def _formatter(md):
            if md is not None:
                for _fmt in formatters:
                    async for result in _fmt(md):
                        yield result
        if not self.counts:
            return _formatter

        self.fmt = '%(count)s\t' + self.fmt
        async def _counter(md):
            if md is None:
                for key, count in self.displayed.items():
                    r = self.result_cache[key]
                    r['count'] = count
                    yield (self.fmt, r)
            else:
                async for r in _formatter(md):
                    pass
        return _counter


class CommandShow(CommandSearch):
    """moggie show [options] <terms>

    JSON output:

      message_dict = { id: match: excluded: filename:[]
                       timestamp: date_relative: tags:[]
                       body[{id: content-type: content:}*]
                       crypto:{} headers:{} }

      message_tuple = ( message_dict, [ message_tuple* ] )

      list of message_tuples

    """
    NAME = 'show'
    ROLES = None
    REAL_ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True

    RE_SIGNED_ID = re.compile(r'^id:[0-9a-f,]+\.[0-9a-zA-Z]+\.[0-9a-zA-Z]+\.[0-9a-f]+$')

    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--q=':              [],
        # These are notmuch options which we implement
        '--format=':         ['text'],
        '--offset=':         ['0'],
        '--limit=':          [''],
        '--part=':           [],
        '--entire-thread=':  [],
        '--include-html':    [],
        '--body=':           ['true'],
        # These are notmuch options which we currently ignore
        '--verify':          [],
        '--decrypt=':        [],
        '--format-version=': [''],
        '--exclude=':        ['true'],
        '--duplicate=':      ['']}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.RE_SIGNED_ID.match(self.terms):
            # First we just extract the ID and update self.terms so signed IDs
            # can be used by authenticated users.
            _id, sig = self.terms.rsplit('.', 1)
            self.terms, aid, ctx = _id.split('.')
            # If auth is lacking, we validate the signature and update...
            if self.worker and self.worker.app and not self.access:
                aid = self.worker.app.config.ACCESS_PREFIX + aid
                ctx = self.worker.app.config.CONTEXT_PREFIX + ctx
                acc = self.worker.app.config.all_access.get(aid)
                if acc:
                    token = acc.check_signature(sig, _id)
                    if token:
                        self.access = acc
                        self.access._live_token = token
                        self.context = ctx
                        logging.debug('Granted %s on %s' % (self.access, self.context))
                if not self.access:
                    logging.warning('Rejecting ID signature: %s (%s, %s)'
                        % (sig, aid, self.terms))

        if not self.access:
            raise PermissionError('Access denied')

        if self.access and self.access is not True:
            if not self.access.grants(self.context, self.REAL_ROLES):
                logging.warning('No access to %s, %s' % (self.context, self.REAL_ROLES))
                raise PermissionError('Access denied')

        self.threads = {}

    async def emit_result_sexp(self, result, first=False, last=False):
        if result is None:
            return
        emitting = await self._buffered_emit(result, first, last)
        if emitting is not None:
            self.print_sexp(emitting)

    async def emit_result_json(self, result, first=False, last=False):
        if result is None:
            return
        emitting = await self._buffered_emit(result, first, last)
        if emitting is not None:
            self.print_json(emitting)

    async def _buffered_emit(self, result, first, last):
        result = result[1]
        idx, pid, tid = result['_id'], result['_parent_id'], result['_thread_id']

        if tid not in self.threads:
            self.threads[tid] = {}
        thread = self.threads[tid]
        thread[idx] = [result, []]

        if last:
            threads = []
            for thread in self.threads.values():
                def _rank(k):
                     r = thread[k][0]
                     if (r['_id'] != r['_parent_id']) and (r['_parent_id'] in thread):
                         return _rank(r['_parent_id']) - 1
                     return 0

                idxs = list(thread.keys())
                idxs.sort(key=_rank)
                for i in idxs:
                    result, kids = thread[i]
                    clean = self._json_sanitize((None, result))[1]
                    idx, pid = result['_id'], result['_parent_id']
                    if (idx != pid) and pid in thread:
                        thread[pid][1].append([clean, kids])
                        del thread[i]
                    else:
                        thread[i][0] = clean

                threads.append(list(thread.values()))

            return threads
        else:
            return None

    def configure(self, *args, **kwargs):
        args = super().configure(*args, **kwargs)
        self.options['--output='] = ['emails']

        if self.options.get('--part='):
            # This is a hack which lets us set the mime-type and filename of
            # our response based on the cid: string generated in _fix_html()
            filename = None
            details = self.options['--part='][-1].split('/')
            if len(details) == 2:
                details, self.filename = details
            else:
                details = details[0]

            details = details.split('-', 3)
            if (len(details) == 4) and (details[0] == 'part'):
                self.mimetype = '%s/%s' % (details[2], details[3])
                if (details[2] == 'text') or (details[3] == 'json'):
                    self.mimetype += '; charset="utf-8"'
                self.options['--part='] = [details[1]]
            else:
                self.mimetype = 'application/octet-stream'

        if not self.terms:
            raise Nonsense('Show what?')
        return args

    async def run(self):
        if self.options.get('--part='):
            self.options['--format='] = ['raw']
        if self.options['--format='][-1] in ('json', 'sexp'):
            self.options['--entire-thread='][:0] = ['true']
        if self.options['--format='][-1] in ('html', 'jhtml'):
            if self.preferences['display_html'] == 'yes':
                self.options['--include-html'] = ['true']
        return await super().run()


class CommandCount(CLICommand):
    NAME = 'count'
    ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--multi':           [],          # Multiple terms as arguments?
        '--format=':         ['text'],    # Also json, sexp!
        '--stdin=':          [],          # Allow lots to send us stdin
        # These are notmuch options which we implement
        '--batch':           [],
        '--input=':          [],
        # These are notmuch options which still need work
        '--output=':         ['messages'],
        '--lastmod':         []}

    def configure(self, args):
        args = self.strip_options(args)
        self.terms = []

        if self.options['--multi']:
            self.terms = args
        elif args:
            self.terms = [' '.join(args)]

        if self.options['--batch']:
            for stdin in self.options['--stdin=']:
                self.terms.extend(ln.strip() for ln in stdin.splitlines())
            if self.options['--input=']:
                for fn in self.options['--input=']:
                    if fn == '-':
                        self.terms.extend(ln.strip() for ln in sys.stdin)
                    else:
                        self.terms.extend(ln.strip() for ln in open(fn, 'r'))
            else:
                self.terms.extend(ln.strip() for ln in self.stdin)

        return []

    async def run(self):
        from ...jmap.requests import RequestCounts

        query = RequestCounts(
            context=self.context,
            terms_list=list(set(self.terms)))
        msg = await self.worker.async_jmap(self.access, query)

        if self.options['--lastmod']:
            suffix = '\tlastmod-unsupported 1'  # FIXME?
        else:
            suffix = ''

        if self.options['--format='][-1] == 'json':
            self.print_json(msg['counts'])
        elif self.options['--format='][-1] == 'sexp':
            self.print_sexp(msg['counts'])
        else:
            for term in self.terms:
                count = msg.get('counts', {}).get(term, 0)
                if self.options['--multi']:
                    self.print('%-10s\t%s' % (count, term))
                else:
                    self.print('%d%s' % (count, suffix))


class CommandEmail(CLICommand):
    """
    Prepare an e-mail for sending, including headers, attachments and
    any encryption or digital signatures.

    Generate headers and optionally a message template for sending a
    new message.

     - Oops, what we actually do is generate the message itself.
     - We want the message template for notmuch compat
     - Being able to generate full messages is more useful though
     - For proper email clients a JSON (or sexp) representation is
       desirable, but we need to be able to receive it back and work
       with that instead of command line args.
     - Do we care to support primitive composition? It's mutt/unix-like
       but gets quite faffy.

    TODO:
     - Think about output formats
     - Accept our output as input?
     - Make forward/reply subcommands work
     - Add PGP and DKIM support, maybe AGE?

    """
    NAME = 'email'
    ROLES = AccessConfig.GRANT_READ
    WEBSOCKET = False
    WEB_EXPOSE = True
    CONNECT = False    # We manually connect if we need to!
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--format=':         ['rfc822'],  # Also text, json, sexp!
        '--stdin=':          [],          # Allow lots to send us stdin

        # These options should all get defaults from the active context.
        '--from=':           [],  # name <e-mail> OR account ID.
        '--signature=':      [],  # A snippet of text to append to the message
        '--sign-as=':        [],  # Auto, Key ID (PGP or DKIM) to sign with
        '--bcc=':            [],  # Hidden recipient (BCC)
        '--header=':         [],  # header:value

        # These may be set indirectly by --reply=
        '--to=':             [],  # To: recipient
        '--cc=':             [],  # Cc: recipient

        '--date=':           [],  # Message date, default is "now"
        '--subject=':        [],  # "message subject"

        '--reply=':          [],  # Search terms
        '--forward=':        [],  # Search terms
        '--decrypt=':        [],  # false, auto, true  (FIXME: notmuch compat)
        '--reply-to=':       ['all'],  # all or sender (notmuch compat)
        '--forwarding=':     [],  # inline (default) or attachment
        '--quoting=':        [],  # html, text, trim, below (multiple allowed)

        '--text=':           [],  # Y, N, "actual text content"
        '--html=':           [],  # Y, N, "actual HTML content"
        '--message=':        [],  # A snippet of text to add to the message
        '--attach=':         [],  # mimetype:/path/to/file

        '--encrypt=':        ['N'], # N, all, attachments
        '--zip-password=':   [],  # Password to use for ZIP encryption
        '--encrypt-to=':     [],  # Auto, Key-IDs (age, PGP, ...) to encrypt to

        '--send-to=':        [],  # E-mails to send to, instead of inferring
                                  # .. from the to/cc/bcc.
        '--send-at=':        [],  # NOW or a time/date to send
        }

    DEFAULT_QUOTING = ['trim']
    DEFAULT_FORWARDING = ['inline']

    def __init__(self, *args, **kwargs):
        self.replying_to = []
        self.forwarding = []
        self.attachments = []
        self.headers = {}
        super().__init__(*args, **kwargs)

    def _load_email(self, fd):
        from moggie.email.parsemime import parse_message
        if fd == sys.stdin.buffer and self.options['--stdin=']:
            data = self.options['--stdin='].pop(0)
        else:
            data = fd.read()
        return parse_message(data, fix_mbox_from=(data[:5] == b'From '))

    def configure(self, args):
        args = self.strip_options(args)

        # FIXME: Accept the same JSON object as we emit; convert it back
        #        to command-line arguments here.
        # FIXME: Accept the same TEXT representation as we emit; convert it
        #        back to command-line arguments here.

        def as_file(key, i, t, target, reader):
            if t[:1] == '-':
                # FIXME: Is this how we handle stdin?
                target.append(reader(sys.stdin.buffer))
                self.options[key][i] = None
            elif (os.path.sep in t) and os.path.exists(t):
                with open(t, 'rb') as fd:
                    target.append(reader(fd))
                self.options[key][i] = None
            # FIXME: Allow in-place base64 encoded data?

        # This lets the caller provide messages for forwarding or replying to
        # directly, instead of searching. Anything left in the reply/forward
        # options after this will be treated as a search term.
        for target, key in (
                  (self.replying_to, '--reply='),
                  (self.forwarding, '--forward=')):
            for i, t in enumerate(self.options[key]):
                as_file(key, i, t, target, self._load_email)
            self.options[key] = [t for t in self.options[key] if t]

        # Similarly, gather attachment data, if it is local. Anything left
        # in the attachment option will be treated as a remote reference.
        key = '--attach='
        for i, t in enumerate(self.options[key]):
            if ':' in t:
                mt, path = t.split(':', 1)
            else:
                mt, path = 'application/octet-stream', t
            as_file(key, i, path, self.attachments,
                lambda fd: (mt, os.path.basename(path), fd.read()))
        self.options[key] = [t for t in self.options[key] if t]

        # Complain if the user attempts both --text= and --message= style
        # composition; we want one or the other!
        if self.options['--message='] and (
                self.options['--text='] or self.options['--html=']):
            raise Nonsense('Use --message= or --text=/--html= (not both)')

        # Parse any supplied dates...
        import datetime
        key = '--date='
        for i, dv in enumerate(self.options[key]):
            try:
                self.options[key][i] = datetime.datetime.fromtimestamp(int(dv))
            except ValueError:
                raise Nonsense('Dates must be Unix timestamps (FIXME)')

        return self.configure2(args)

    def _get_terms(self, args):
        """Used by Reply and Forward"""
        if '--' in args:
            pos = args.indexOf('--')
            if pos > 0:
                raise Nonsense('Unknown args: %s' % args[:pos])
            args = args[(pos+1):]
        else:
            opts = [a for a in args if a[:2] == '--']
            args = [a for a in args if a[:2] != '--']
            if pos > 0:
                raise Nonsense('Unknown args: %s' % opts)
        return args

    def configure2(self, args):
        if args:
            raise Nonsense('Unknown args: %s' % args)
        return args

    def text_part(self, text, mimetype='text/plain'):
        try:
            data = str(bytes(text, 'us-ascii'), 'us-ascii')
            enc = '7bit'
        except UnicodeEncodeError:
            import email.base64mime as b64
            #data = b64.body_encode(bytes(text, 'utf-8'))
            data = text  # FIXME: Delete this
            enc = 'base64'
        return ({
                'content-type': [mimetype, ('charset', 'utf-8')],
                'content-disposition': 'inline',
                'content-transfer-encoding': enc
            }, data)

    def multi_part(self, mtype, parts):
        from moggie.email.headers import format_headers
        from moggie.util.mailpile import b64c, sha1b64
        import os
        boundary = b64c(sha1b64(os.urandom(32)))
        bounded = ['\r\n--%s\r\n%s%s' % (
                boundary,
                format_headers(headers),
                body
            ) for headers, body in parts]
        bounded.append('\r\n--%s--' % boundary)
        return ({
                'content-type': [
                    'multipart/%s' % mtype, ('boundary', boundary)],
                'content-transfer-encoding': '7bit'
            }, '\r\n'.join(bounded).strip())

    def attach_part(self, mimetype, filename, data):
        import email.base64mime as b64
        ctyp = [mimetype]
        disp = ['attachment']
        if filename:
            disp.append(('filename', filename))
        return ({
                'content-type': ctyp,
                'content-disposition': disp,
                'content-transfer-encoding': 'base64'
            }, b64.body_encode(data).strip())

    def get_encryptor(self):
        return None, ''

    def get_passphrase(self):
        if self.options['--zip-password=']:
            return bytes(self.options['--zip-password='][-1], 'utf-8')
        # FIXME: Generate a password? How do we tell the user?
        raise Nonsense('FIXME: need a password')
        return None

    def attach_encrypted_attachments(self, text_parts=None):
        from moggie.storage.exporters.maildir import ZipWriter
        import io, base64

        mimetype = 'application/octet-stream'
        filename = 'message.zip' if text_parts else 'attachments.zip'
        encryptor, ext = self.get_encryptor()
        passphrase = None if encryptor else self.get_passphrase()

        now = time.time()
        fd = io.BytesIO()
        zw = ZipWriter(fd, password=passphrase)
        if text_parts:
            for headers, b64data in text_parts:
                if headers['content-type'][0] == 'text/html':
                    fn = 'message.html'
                else:
                    fn = 'message.txt'
                zw.add_file(fn, now, base64.b64decode(b64data))
        for _unused, fn, data in self.attachments:
            zw.add_file(fn, now, data)
        zw.close()
        data = fd.getvalue()

        # If we are PGP or AGE encrypting the file, that transformation
        # happens here.
        if encryptor:
            filename += '.%s' % ext
            data = encryptor(data)

        return self.attach_part(mimetype, filename, data)

    def wrap_text(self, txt):
        lines = ['']
        for word in txt.replace('\r', '').replace('\n', ' ').split():
            if len(lines[-1]) + len(word) >= 72:
                lines.append('')
            lines[-1] += ' ' + word
        return '\r\n'.join(l.strip() for l in lines if l)

    def html_to_text(self, html):
        from moggie.security.html import html_to_markdown
        return html_to_markdown(html, wrap=72)

    def text_to_html(self, text):
        import markdown
        return markdown.markdown(text)

    def text_and_html(self, msg, is_html=None):
        msg = msg.strip()
        if is_html is True or (is_html is None and msg.startswith('<')):
            return self.html_to_text(msg), msg
        else:
            return msg, self.text_to_html(msg)

    def get_message_text(self, message, mimetype='text/plain'):
        message.with_text()
        print('%s' % message)
        found = []
        for part in message['_PARTS']:
            if part['content-type'][0] == mimetype and '_TEXT' in part:
                found.append(part['_TEXT'])
        return '\n'.join(found)

    def collect_quotations(self, message):
        import time
        import email.utils
        from moggie.security.html import HTMLCleaner

        #when = ' '.join(message['date'].strip().split()[:-2])
        if message['from']['fn']:
            frm = '%(fn)s <%(address)s>' % message['from']
        else:
            frm = message['from']['address']

        strategy = self.options['--quoting='] or self.DEFAULT_QUOTING
        quote_text = quote_html = ''

        def _quotebrackets(txt):
            return ''.join('> %s' % l for l in txt.strip().splitlines(True))
        quote_text = _quotebrackets(self.get_message_text(message))

        if 'html' in strategy or ('text' not in strategy and not quote_text):
            quote_html = self.get_message_text(message, mimetype='text/html')
            if quote_html:
                quote_html = '<blockquote>%s</blockquote>' % quote_html

        if 'trim' in strategy:
            pass  # Trim the quoted text a bit

        if quote_text and not quote_html:
            # Note: _quotebrackets becomes <blockquote>
            quote_html = self.text_to_html(quote_text)
        elif quote_html and not quote_text:
            # FIXME: Should html_to_text instead convert <blockquotes> to >>> ?
            quote_text = _quotebrackets(self.html_to_text(quote_html))

        if quote_text:
            quote_text = '%s wrote:\n%s' % (frm, quote_text)
        if quote_html:
            # FIXME: add our own CSS definitions, which the cleaner will then
            #        apply for prettification?
            quote_html = HTMLCleaner('<p>%s wrote:</p>\n%s' % (
                    frm.replace('<', '&lt;').replace('>', '&gt;'),
                    quote_html)
                ).close()

        return strategy, quote_text, quote_html

    def collect_inline_forwards(self, message):
        strategy = self.options['--forwarding='] or self.DEFAULT_FORWARDING
        if 'inline' in strategy:
            pass  # Extract text, add it
        return strategy, fwd_text, fwd_html

    def generate_text_parts(self, want_text, want_html):
        text, html = [], []

        quoting = {}
        for msg in self.options['--message=']:
            t, h = self.text_and_html(msg)
            text.append(t)
            html.append(self.wrap_text(h))

        for msg in self.replying_to:
            strategy, q_txt, q_htm = self.collect_quotations(msg)
            if q_txt and q_htm:
                if 'below' in strategy:
                    text[:0] = [q_txt]
                    html[:0] = [q_htm]
                else:
                    text.append(q_txt)
                    html.append(q_htm)

        for msg in self.forwarding:
            strategy, f_txt, f_htm = self.collect_inline_forwards(msg)
            if f_txt and f_htm:
                text.append(f_txt)
                html.append(f_htm)

        for sig in self.options['--signature=']:
            t, h = self.text_and_html(sig)
            text.append('-- \r\n' + t)
            html.append('<br><br>--<br>\n' + self.wrap_text(h))

        if not want_text:
            text = []
        if not want_html:
            html = []
        return text, html

    def render(self):
        from moggie.email.addresses import AddressHeaderParser
        from moggie.email.headers import HEADER_CASEMAP, format_headers

        for hdr_val in self.options['--header=']:
            hdr, val = hdr_val.split(':', 1)
            if hdr.lower() in HEADER_CASEMAP:
                hdr = hdr.lower()
            h = self.headers[hdr] = self.headers.get(hdr, [])
            h.append(val)

        for hdr, opt, parse in (
                ('from',    '--from=',    lambda a: AddressHeaderParser(a)),
                ('to',      '--to=',      lambda a: AddressHeaderParser(a)),
                ('cc',      '--cc=',      lambda a: AddressHeaderParser(a)),
                ('date',    '--date=',    lambda v: v),
                ('subject', '--subject=', lambda v: v)):
            val = [parse(t) for t in self.options[opt]]
            h = self.headers[hdr] = self.headers.get(hdr, [])
            for v in val:
                # Someone should spank me for playing golf
                (h.extend if isinstance(v, list) else h.append)(v)
            if not h:
                del self.headers[hdr]

        if 'date' not in self.headers:
            import datetime
            self.headers['date'] = [datetime.datetime.now()]

        if 'mime-version' not in self.headers:
            self.headers['mime-version'] = 1.0

        # Sanity checks
        if len(self.headers.get('from', [])) != 1:
            raise Nonsense('There must be exactly one From address!')
        if len(self.headers.get('date', [])) > 1:
            raise Nonsense('There can only be one Date!')

        msg_opt = self.options['--message=']
        text_opt = self.options['--text=']
        want_text = (msg_opt or text_opt) and (['N'] != text_opt)

        html_opt = self.options['--html=']
        want_html = (msg_opt or html_opt) and (['N'] != html_opt)

        if html_opt and 'Y' in text_opt:
            text_opt.append(self.html_to_text('\n\n'.join(html_opt)))

        elif text_opt and 'Y' in html_opt:
            html_opt.append(self.text_to_html('\n\n'.join(text_opt)))

        else:
            text_opt, html_opt = self.generate_text_parts(want_text, want_html)

        # FIXME: Is this where we fork, on what the output format is?

        parts = []
        text_opt = [t for t in text_opt if t not in ('', 'Y')]
        if want_text and text_opt:
            parts.append(self.text_part(
                '\r\n\r\n'.join(t.strip() for t in text_opt)))

        html_opt = [t for t in html_opt if t not in ('', 'Y')]
        while 'Y' in html_opt:
            html_opt.remove('Y')
        if want_html and html_opt:
            parts.append(
                self.text_part(
                    '\r\n\r\n'.join(html_opt),
                    mimetype='text/html'))

        encryption = self.options['--encrypt='][-1].lower()
        if encryption == 'all' and not self.options['--encrypt-to=']:
            # Create an encrypted .ZIP with entire message content
            parts = [self.attach_encrypted_attachments(text_parts=parts)]
        else:
            if len(parts) > 1:
                parts = [self.multi_part('alternative', parts)]

            if encryption == 'attachments':
                # This will create an encrypted .ZIP with our attachments only
                parts.append(self.attach_encrypted_attachments())
            else:
                for mimetype, filename, data in self.attachments:
                    parts.append(self.attach_part(mimetype, filename, data))

        if len(parts) > 1:
            parts = [self.multi_part('mixed', parts)]

        if encryption == 'all' and self.options['--encrypt-to=']:
            # Encrypt to someone: a PGP or AGE key
            parts = [self.encrypt_to_recipient(parts)]

        self.headers.update(parts[0][0])
        body = parts[0][1]

        return ''.join([format_headers(self.headers), body])

    def _reply_addresses(self):
        senders = {}
        recipients = {}
        def _add(_hash, _ai):
             _hash[_ai.address] = _ai
        for email in self.replying_to:
             _add(senders, email['from'])
             for ai in email.get('to', []) + email.get('cc', []):
                 _add(recipients, ai)
        return senders, recipients

    def gather_from(self, senders_and_recipients=None):
        senders, recipients = senders_and_recipients or self._reply_addresses()

        # FIXME: Check the current context for addresses that were on the
        #        recipient list. If none are found, use the main address for
        #        the context.
        print('Candidates: %s' % recipients)

        # FIXME: Check the replied messages, are any of those e-mails
        #        ours?
        raise Nonsense('FIXME: No From')

    def gather_to_cc(self, senders_and_recipients=None):
        senders, recipients = senders_and_recipients or self._reply_addresses()

        from moggie.email.addresses import AddressHeaderParser
        frm = AddressHeaderParser(self.options['--from='][0])[0].address

        self.options['--to='].extend(
            a.normalized() for a in senders.values() if a.address != frm)
        if self.options['--reply-to='][-1] == 'all':
            self.options['--cc='].extend(
                a.normalized() for a in recipients.values()
                if a.address != frm and a.address not in senders)

    async def gather_emails(self, searches):
        emails = []
        for search in searches:
            print('FIXME: Search for %s' % search)
        return emails

    async def gather_attachments(self, searches):
        atts = []
        for search in searches:
            print('FIXME: Find attachment for %s' % search)
        return atts

    async def run(self):
        for target, key, gather in (
                (self.replying_to, '--reply=',   self.gather_emails),
                (self.forwarding,  '--forward=', self.gather_emails),
                (self.attachments, '--attach=',  self.gather_attachments)):
            if self.options[key]:
                target.extend(await gather(self.options[key]))

        if not self.options['--from=']:
            self.gather_from()

        if not self.options['--to='] and not self.options['--cc=']:
            self.gather_to_cc()

        self.print(self.render())


class CommandCompose(CommandEmail):
    """
    Generate headers and optionally a message template for replying
    to a set of messages.

    FIXME
    """
    NAME = 'compose'


class CommandReply(CommandCompose):
    """
    Generate headers and optionally a message template for replying
    to a set of messages.

    When replying to multiple messages, the headers will be constructed
    with the assumption that the most recent message is being replied to,
    but others are being quoted and referenced for context.

    Message templates are simple non-MIME plain/text messages which
    can be edited directly using a text editor. For more complex message
    structures (multipart, HTML text), applications should use the JSON
    format and construct their own message using the provided data.

    IDEAS:
       ... add a mode where the caller can specify the user's response
           and queue for sending directly. This will let us cleanly
           generate HTML mail, using a "standard but abhorred" top-posting
           style.
       ... allow the user to specify attachments on the command line
    """
    NAME = 'reply'

    def configure2(self, args):
        self.options['--reply='].extend(self._get_terms(args))
        return []


class CommandForward(CommandCompose):
    """
    This command behaves simplarly to `moggie reply`, except it assumes
    the user is forwarding the matching messages instead of replying to
    them.

    IDEAS:
       ... Support bounce or resend, where the messages are not modified
           at all, just resent, one at a time.
       ... Allow the user to request messages be forwarded as message/rfc822
           attachments.
    """
    NAME = 'forward'

    def configure2(self, args):
        self.options['--forward='].extend(self._get_terms(args))
        return []



class CommandTag(CLICommand):
    """# moggie tag [options] +<tag>|-<tag> [...] -- <search terms ...>

    Tag or untag e-mails matching a particular search query. Examples:

        moggie tag +family -- to:bjarni from:dad
        moggie tag --context=Personal -play +school -- homework

    Alternately, instead of a search query a JSON object can be used to
    add or remove metadata on the tags themselves:

        moggie tag +family -- 'META={"name": "My Family"}'
        moggie tag +family +school -- 'META={"parent": "personal"}'

    Options:

        --context=<C>      Set the context for the operation
        --batch            Read stdin for tag commands, one per line
        --input=<filename> Read batch commands from a file
        --comment=<C>      Add a comment to the tag-history for this op
        --remove-all       Strip all tags from matching messages
        --undo=<ID>        Undo a recent tag operation
        --redo=<ID>        Redo a recent tag operation

    For the purposes of recording history and facilitating undo, we treat
    all operations within a single batch as one; all searches are performed
    before any tagging takes place and which means the entire batch op can
    be undone in one step as well.

    This means a batch like this:

        +inbox -unread -incoming -- in:incoming
        +potato -- in:incoming

    ... will tag all the messages as 'in:potato', even though the first
    line strips the 'in:incoming' tag and the second would be a no-op if
    they were done one after another.

    Batches can have in-line trailing comments using a '#' sign, but it
    must be both preceded and followed by a space: # like this.

    To remove all matches for a single search within a batch, use '-*'
    as a tag operation.

    Note that tag metadata changes cannot be undone.
    """
    NAME = 'tag'
    ROLES = AccessConfig.GRANT_READ + AccessConfig.GRANT_TAG_RW
    MAX_TAGOPS = 5000
    WEB_EXPOSE = True
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--format=':         [None],
        '--comment=':        [None],
        '--stdin=':          [],          # Allow lots to send us stdin
        '--undo=':           [None],
        '--redo=':           [None],
        '--big':             [],         # Big batch?
        # These are notmuch options which we implement
        '--batch':           [],
        '--input=':          [],
        '--remove-all':      []}

    def _validate_and_normalize_tagops(self, tagops):
        for idx, tagop in enumerate(tagops):
            otagop = tagop
            if tagop[:1] not in ('+', '-'):
                raise Nonsense(
                    'Tag operations must start with + or -: %s' % otagop)
            if tagop[1:4] in ('in:',):
                tagop = tagops[idx] = tagop[:1] + tagop[4:]
            elif tagop[1:5] in ('tag:',):
                tagop = tagops[idx] = tagop[:1] + tagop[5:]
            if not tagop[1:]:
                raise Nonsense('Missing tag: %s' % otagop)
            tagops[idx] = tag_unquote(tagop).lower()
        if self.options['--remove-all'] and '-*' not in tagops:
            tagops.insert(0, '-*')

    def _batch_configure(self, ifd):
        for line in ifd:
            line = line.strip()
            if line and not line.startswith('#'):
                tagops, terms = line.split('--')
                terms = terms.strip()
                tagops = tagops.strip().split()
                self._validate_and_normalize_tagops(tagops)
                if terms.startswith('META={'):
                    yield (tagops, json.loads(terms[5:]))
                else:
                    yield (tagops, terms.split(' # ')[0].strip())

    def configure(self, args):
        self.tagops = []
        argtext = 'tag %s' % ' '.join(args)
        if '--' in args:
            ofs = args.index('--')
            tags = self.strip_options(args[:ofs])
            terms = args[ofs+1:]
        else:
            tags, terms = self.strip_options(args), []

        self.desc = self.options['--comment='][-1] or argtext

        if self.options['--batch'] and not self.options['--input=']:
            self.options['--input='].append('-')
        for fn in set(self.options['--input=']):
            if fn == '-':
                for stdin in self.options['--stdin=']:
                    self.tagops.extend(
                        self._batch_configure(stdin.splitlines()))
                self.tagops.extend(self._batch_configure(self.stdin))
            else:
                with open(fn, 'r') as fd:
                    self.tagops.extend(self._batch_configure(fd))

        if (len(self.tagops) > self.MAX_TAGOPS) and not self.options['--big']:
            raise Nonsense(
                'Too many operations (max=%d), use --big' % self.MAX_TAGOPS)

        if not (self.options['--input=']
                or self.options['--redo='][-1]
                or self.options['--undo='][-1]):
            while tags and tags[-1][:1] not in ('+', '-'):
                terms[:0] = [tags.pop(-1)]
            self._validate_and_normalize_tagops(tags)

            if not tags or not terms:
                raise Nonsense('Nothing to do?')

            terms = ' '.join(terms)
            if terms.startswith('META={'):
                terms = json.loads(terms[5:])
            self.tagops = [(tags, terms)]

        elif (self.options['--redo='][-1]
                 and not self.options['--undo='][-1]
                 and not (self.tagops or tags or terms)):
            pass

        elif (self.options['--undo='][-1]
                 and not self.options['--redo='][-1]
                 and not (self.tagops or tags or terms)):
            pass

        elif tags or terms:
            raise Nonsense(
                'Use batches, undo/redo, or the command line, not both')

        return []

    async def run(self):
        if self.options['--big'] and len(self.tagops) > self.MAX_TAGOPS:
            count = 0
            total = len(self.tagops)
            while self.tagops:
                batch = self.tagops[:self.MAX_TAGOPS]
                self.tagops[:len(batch)] = []
                self.print('# Batch %d..%d of %d'
                    % (count, count + len(batch) - 1, total))
                await self.run_batch(batch)
                count += len(batch)
        else:
            await self.run_batch(self.tagops)

    async def run_batch(self, tagops):
        from ...jmap.requests import RequestTag

        query = RequestTag(
            context=self.context,
            undoable=(not self.options['--redo='][-1]
                and not self.options['--undo='][-1]
                and self.desc),
            tag_undo_id=self.options['--undo='][-1],
            tag_redo_id=self.options['--redo='][-1],
            tag_ops=tagops)
        msg = await self.worker.async_jmap(self.access, query)

        fmt = self.options['--format='][-1]
        if fmt == 'json':
            self.print_json(msg['results'])
        elif fmt == 'jhtml':
            self.print_jhtml(msg['results'])
        elif fmt == 'html':
            self.print_html(msg['results'])
        elif fmt == 'sexp':
            self.print_sexp(msg['results'])
        elif 'history' in msg['results']:
            self.print(
                'Tagged:\t\t%(comment)s\nChange ID:\t%(id)s'
                % msg['results']['history'])
        else:
            self.print('%s' % msg['results'])


def CommandConfig(wd, args):
    cfg = AppConfig(wd)
    if len(args) < 1:
        print('%s' % cfg.filepath)

    elif args[0] == 'get':
        section = args[1]
        options = args[2:]
        if not options:
            options = cfg[section].keys()
        print('[%s]' % (section,))
        for opt in options:
            try:
                print('%s = %s' % (opt, cfg[section][opt]))
            except KeyError:
                print('# %s = (unset)' % (opt,))

    elif args[0] == 'set':
        try:
            section, option, value = args[1:4]
            cfg.set(section, option, value, save=True)
            print('[%s]\n%s = %s' % (section, option, cfg[section][option]))
        except KeyError:
            print('# Not set: %s / %s' % (section, option))
