# These are CLI commands which aim to behave as similarly to notmuch as
# possible. Because why not? Compatibility is nice.
#
# TODO: Look into tabular
#       Refactor `moggie show` to extend `moggie parse`
#
# These are the commands used by the dodo mail client:
#    notmuch new
#    notmuch tag +replied -- id:<MSGID>
#    notmuch search --format=json <QUERY>
#    notmuch tag <EXPRESSION> -- id:<MSGID>
#    notmuch search --output=tags *
#    notmuch count --output=threads -- tag:<TAG>
#    notmuch count --output=threads -- tag:<TAG> AND -tag:read
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
import logging
import io
import os
import re
import sys
import time

from .command import Nonsense, CLICommand, AccessConfig
from .email import CommandEmail, CommandParse
from ...config import AppConfig
from ...email.addresses import AddressInfo
from ...email.parsemime import MessagePart
from ...email.metadata import Metadata
from ...email.sync import generate_sync_id, parse_sync_info
from ...api.exceptions import *
from ...api.requests import *
from ...security.mime import part_filename, magic_part_id
from ...security.html import clean_email_html
from ...storage.exporters.mbox import MboxExporter
from ...storage.exporters.maildir import MaildirExporter, EmlExporter
from ...storage.exporters.msgdirs import MsgdirsExporter
from ...util.mailpile import tag_unquote
from ...util.dumbcode import dumb_decode, to_json, from_json


def _html_quote(t):
    return (t
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;'))


class CommandSearch(CLICommand):
    """# moggie search [options] <search terms ...>

    Search for emails or threads matching the given search terms, returning
    matching data in a variety of formats.

    Search terms are exact matches, unless the wildcard (*) is used.

    ### Examples

        moggie search bjarni                 # Exact match
        moggie search bjarn*                 # Will match bjarni or bjarna

        moggie search in:inbox -tag:read     # In the inbox, still unread
        moggie search in:inbox tag:read      # In the inbox, already read
        moggie search -tag:read +in:inbox    # Unread or in the inbox
        moggie search bjarni --format=json   # JSON for further processing...

        moggie search dates:2022-08 --format=mbox > August2022.mbx  # Export!

    See also `moggie help how-to-search` for more details and examples.

    ### Search options

    %(search)s

    ### Output options

    The search command can emit various types of results in various formats,
    depending on these options:

    %(output)s

    The outputs can be:

       * `summary` - A summary of threads matching the search
       * `threads` - A list of thread IDs matching the search
       * `messages` - A list of moggie message IDs matching the search
       * `files` - A list of file paths matching the search
       * `tags` - A list of tags found on messages matching the search
       * `emails` - Entire e-mails matching the search
       * `thread_emails` - Entire e-mails from threads matching the search
       * `metadata` - Moggie's metadata about the messages
       * `thread_metadata` - Metadata for entire threads matching the search

    Supported formats:

       * `text` - More-or-less human-readable text
       * `text0` - The same as text, but deliminated by null characters
                   instead of newlines.
       * `json` - A structured JSON object
       * `sexp` - The same contents as JSON outputs, but as an S-expression
       * `zip` - Entire messages as individual files in a ZIP archove
       * `maildir` - Entire messages in a Maildir mailbox, in a .TGZ archive
       * `mailzip` - Entire messages in a Maildir mailbox, in a .ZIP archive
       * `msgdirs` - Messages converted to files+directories, in a .TGZ archive
       * `msgdzip` - Messages converted to files+directories, in a .ZIP archive
       * `mbox` - Entire messages in a Unix mbox mailbox

    Notes:

       * The default output is `summary`, unless something else is implied
         by the format. The default format is `text`.
       * The only valid outputs for zip, maildir and mbox are emails and
         thread_emails.
       * The headers of messages contained in mbox and zip results will be
         modified to include Moggie metadata (tags, read/unread, etc.).
       * Searching for `*` returns all known mail.
       * Searching for `mailbox:/path/to/mailbox` can be used to extract
         information from a mailbox directly.
       * File listings may not encode to Unicode correctly, since *nix
         filenames are in fact binary data, not UTF-8. This means JSON
         formatting with `--output=files` may fail in some cases. Use
         `--format=text0` for the most reliable results.

    ### Notmuch compatibility

    Where moggie and notmuch options overlap (see `man notmuch`), an attempt
    has been made to ensure compatibility. However, note that Moggie file
    paths have extra data appended (offets within a mbox, etc). Moggie's
    search syntax also differs from that of notmuch in important ways.
    """
    __NOTES__ = """

FIXME: Document html and html formats!

"""
    NAME = 'search'
    ROLES = AccessConfig.GRANT_READ
    WEBSOCKET = False
    WEB_EXPOSE = True
    HTML_DEFAULT_LIMIT = 25
    HTML_COLUMNS = ['count', 'thread', 'address', 'name', 'authors',
                    'tags', 'subject', 'date_relative']
    OPTIONS = [[
        (None, None, 'search'),
        ('--context=',   ['default'], 'The context for scope and settings'),
        ('--stdin=',              [], None), # Emulate stdin on API
        ('--q=',                  [], 'Search terms (used by web API)'),
        ('--qr=',                 [], 'Refining terms (used by web API)'),
        ('--or',             [False], 'Use OR instead of AND with search terms'),
        ('--offset=',          ['0'], 'Skip the first X results'),
        ('--limit=',            [''], 'Output at most X results'),
        ('--tabs',           [False], 'Separate with tabs instead of spaces'),
        ('--entire-thread=',      [], 'X=(true|false)'),
        ('--username=',       [None], 'Username with which to access email'),
        ('--password=',       [None], 'Password with which to access email'),
        ('--json-ui-state',       [], 'Include UI state in JSON result'),
    ],[
        (None, None, 'output'),
        ('--format=',       ['text'], 'X=(text*|text0|json|sexp|zip|maildir|mbox|..)'),
        ('--output=',    ['default'], 'X=(summary*|threads|messages|files|emails|..)'),
        ('--indent=',          [' '], ''),
        ('--zip-password=',   [None], 'Password for encrypted ZIP exports'),
        ('--sync-src=',       [None], 'Source for generating sync IDs'),
        ('--sync-dest=',      [None], 'Destination for generating sync IDs'),
        ('--export-to=',      [None], 'Local mailbox to export to'),
        # These are notmuch options which we currently ignore
        ('--sort=', ['newest-first'], ''),  # notmuch: ignored
        ('--format-version=',     [], ''),  # notmuch: ignored
        ('--exclude=',      ['true'], ''),  # notmuch: ignored
        ('--duplicate=',        [''], '')]] # notmuch: ignored

    def __init__(self, *args, **kwargs):
        self.displayed = {}
        self.default_output = 'summary'
        self.with_result_meta = False
        self.fake_tid = int(time.time() * 1000)
        self.raw_results = None
        self.exporter = None
        self.mailboxes = None
        self.sync_dest = self.sync_src = self.sync_id = None
        self.terms = None
        super().__init__(*args, **kwargs)

    def configure(self, args):
        self.batch = 10000

        # Allow --q=.., --qr= and unmarked query terms.
        # Both --q= and --qr= are mostly for use with the web-CLI, and
        # are reported separately in the webui_state.
        terms = self.strip_options(args)
        terms.extend(self.options['--q='])
        terms.extend(self.options.get('--qr=', []))

        self.mailboxes, terms = self.remove_mailbox_terms(terms)
        self.terms = self.combine_terms(terms)  # Respects --or

        if self.mailboxes and ('-' in self.mailboxes):
            mailbox = self.get_tempfile()
            mailbox.write(self.read_file_or_stdin(self, '-', _bytes=True))
            mailbox.flush()
            self.mailboxes[self.mailboxes.index('-')] = mailbox.name
            if not self.options.get('--sync-src=', [None])[-1]:
                self.options['--sync-src='] = ['stdin']

        fmt = self.options['--format='][-1]
        if fmt in ('json', 'jhtml'):
            self.mimetype = 'application/json'
        elif fmt == 'html':
            self.mimetype = 'text/html; charset=utf-8'
        elif fmt == 'mbox':
            self.mimetype = 'application/mbox'
        elif fmt in ('zip', 'mailzip', 'msgdzip'):
            self.mimetype = 'application/zip'
        elif fmt in ('maildir', 'msgdirs'):
            self.mimetype = 'application/x-tgz'

        if fmt in ('html', 'jhtml') and not self.options['--limit='][-1]:
            self.options['--limit='].append(self.HTML_DEFAULT_LIMIT)

        if self.options['--format='][-1] in (
                'maildir', 'mailzip', 'zip', 'mbox', 'msgdirs', 'msgdzip'):
            self.default_output = 'emails'

        def _np(t):
            if t is None:
                return t
            slash = ('/' if (t and t.endswith('/')) else '')
            return (os.path.normpath(t) if t else '') + slash

        self.sync_src = _np(self.options.get('--sync-src=', [None])[-1])
        self.sync_dest = _np(self.options.get('--sync-dest=', [None])[-1])
        self.export_to = _np(self.options.get('--export-to=', [None])[-1])
        if self.export_to and not self.sync_dest:
            self.sync_dest = self.export_to
        if not self.sync_src:
            if self.mailboxes:
                self.sync_src = 'mailbox:' + ', '.join(self.mailboxes)
            elif self.terms:
                self.sync_src = self.terms

        self.validate_configuration()
        self.preferences = self.cfg.get_preferences(context=self.context)
        return []

    def validate_configuration(self, output=True, zip_encryption=True):
        if output:
            output = self.options['--output=']
            if 'result-meta' in output:
                self.with_result_meta = True
                output.remove('result-meta')
            if len(output) > 1:
                if 'default' in output:
                    output.remove('default')
            if len(output) > 1:
                raise Nonsense('Please only request one type of output')

        if zip_encryption:
            if ((self.options.get('--zip-password=') or [None])[-1] and
                   self.options['--format='][-1] not in ('zip', 'mailzip')):
                raise Nonsense('Encryption is only supported with ZIP formats')

    async def as_metadata(self, md):
        if md is not None:
            yield ('%s', Metadata(*md).parsed())

    async def as_threads_metadata(self, thread):
        if thread is not None:
            hits = thread['hits']
            for md in thread['messages']:
                md = Metadata(*md).parsed()
                md['is_hit'] = md['idx'] in hits
                yield ('%s', md)

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
        if not thread.get('hits'):
            return

        try:
            sep = '\t' if self.options['--tabs'][-1] else ' '
            mid = tid = thread['thread']
            msgs = dict((i[1] or tid, Metadata(*i).parsed())
                for i in thread['messages'])

            top = msgs.get(tid, {})
            md = msgs.get(thread['hits'][0], {})

            # If we have a syn_idx, that means we are searching within a
            # mailbox and should emit IDs that will work with that search.
            mid = md.get('syn_idx', mid)

            try:
                ts = min(msgs[i]['ts'] for i in thread['hits'] if i in msgs)
            except ValueError:
                ts = 0
            fc = sum(len(m['ptrs']) for m in msgs.values())

            tags = []
            for msg in msgs.values():
                tags.extend(msg.get('tags', []))
            tags = [t.split(':')[-1] for t in set(tags)]

            authors = ', '.join(list(set(
                (m['from']['fn'] or m['from']['address'])
                for m in msgs.values() if 'from' in m)))

            annotations = dict((k[1:], v) for k,v in md.items() if k[:1] == '=')

            info = {
                '_sep': sep,
                'thread': '%8.8d' % tid,
                'timestamp': ts,
                'date_relative': self._relative_date(ts),
                'matched': len(thread['hits']),
                'total': len(msgs),
                'files': fc,
                'authors': authors,
                'subject': top.get('subject', md.get('subject', '(no subject)')),
                'query': [self.sign_id('id:%s' % ','.join('%d' % mid for mid in msgs))] + [None],
                'tags': tags,
                'annotations': annotations}
            info['_url_thread'] = '/cli/show/%s' % info['query'][0]
            info['_tag_list'] = '%s(%s)' % (sep, ' '.join(tags)) if tags else ''
            info['_file_count'] = '(%d)' % fc if (fc > len(msgs)) else ''
            info['_id'] = (
                ('id:%12.12d' % mid) if (len(msgs) == 1) else
                ('thread:%s' % tid))

            yield (
                '%(_id)s%(_sep)s%(date_relative)s'
                '%(_sep)s[%(matched)s/%(total)s%(_file_count)s]'
                '%(_sep)s%(authors)s;'
                '%(_sep)s%(subject)s%(_tag_list)s',
                info)
        except:
            logging.exception('Failed to render: %s' % (thread,))

    async def as_sync_info(self, md):
        if md is None:
            return

        if (not self.sync_id) and self.sync_dest and self.sync_src:
            self.sync_id = generate_sync_id(
                self.worker.unique_app_id, self.sync_src, self.sync_dest)

        md = Metadata(*md)
        fn = dumb_decode(md.pointers[0].ptr_path) if md.pointers else None
        uuid = md.uuid_asc
        sync_info = md.get_sync_info()
        sync_info_parsed = parse_sync_info(sync_info, self.sync_id) if sync_info else None
        if self.sync_src == 'stdin':
            fn = '-'
        elif fn:
            try:
                fn = str(fn, 'utf-8')
            except UnicodeDecodeError:
                fn = str(fn, 'latin-1')
        yield ('%(id)s\t%(uuid)s\t%(_file_str)s\t%(_sync_info_str)s', {
            'id': self.sign_id('id:%8.8d' % md.idx),
            'file': fn,
            'uuid': uuid,
            'indexed': (md.idx < 100000000),
            'sync_info': sync_info_parsed,
            '_file_str': fn or '',
            '_sync_info_str': str(sync_info or b'', 'utf-8')})

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

    def _fix_html(self, metadata, msg, part):
        show_ii = (self.preferences['display_html_inline_images'] == 'yes')
        show_ri = (self.preferences['display_html_remote_images'] == 'yes')
        t_blank = (self.preferences['display_html_target_blank'] == 'yes')
        return clean_email_html(metadata, msg['email'], part,
             id_signer=self.sign_id,
             inline_images=show_ii,
             remote_images=show_ri,
             target_blank=t_blank)

    async def as_emails(self, thread, fmt=None):
        def _textify(r, prefer, _all, esc, pesc, part_fmt, msg_fmt, hdr_fmt='%(h)s: %(v)s'):
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

                    options = [(len(parts[i].get('content', '')), i)
                        for i, t in enumerate(types) if t == prefer]

                    if len(options) == 1:
                        preferred = options[0][1]
                    elif len(options) > 1:
                        preferred = max(options)[1]
                    else:
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
                            _part(sp) for sp in _classify(ct, p['content'])
                            if _all or 'mHide' not in sp.get('class', ''))
                    else:
                        p['content'] = pesc(p, p['content']).rstrip() + '\n'
                else:
                    p['content'] = '(%s)' % ((p['_fn'] or 'Non-text part: ') + p['_ct'])
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

        def _as_simple_text(r):
            indent = self.options['--indent='][-1]
            prefix = '' if (indent is True) else indent
            joiner = '\n' + prefix
            def _indent(part, txt):
                return prefix + joiner.join(txt.splitlines())
            return _textify(r, 'text/plain', False, lambda t: t, _indent,
                """%(content)s""",
                """\
X-EMAIL: id:%(i)s depth:%(d)d path:%(f)s
Tags: %(t)s
%(h)s

%(b)s""")

        def _as_notmuch_text(r):
            return _textify(r, 'text/plain', True, lambda t: t, lambda p,t: t,
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
            return _textify(r, 'text/html', True, _html_quote, lambda p,t: t,
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
            fmt = fmt or self.options['--format='][-1]
            part = int((self.options.get('--part=') or [0])[-1])
            raw = (fmt in ('raw', 'zip', 'maildir', 'mailzip', 'mbox'))
            want_body = raw or (self.options.get('--body=', [0])[-1] != 'false')
            want_html = bool((fmt in ('json', 'html', 'jhtml'))
                or self.options.get('--include-html'))
            shown_types = ('text/plain', 'text/html') if want_html else ('text/plain',)

            notmuch_compatible = bool(self.options.get('--format-version='))
            thread = self._as_thread(thread)
            for md in thread['messages']:
              try:
                md = Metadata(*md)
                if want_body:
                    query = RequestEmail(
                        metadata=md,
                        data=(True if part else False),
                        parts=([part-1] if part else None),
                        full_raw=(not part),
                        username=self.options['--username='][-1],
                        password=self.options['--password='][-1])
                    query['context'] = self.context
                    msg = await self.worker.async_api_request(
                        self.access, query)
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

                elif raw:
                    yield ('',
                        {'_metadata': md, '_data': msg['email']['_RAW']})

                else:
                    # FIXME:
                    #   - remove duplicate logic, rely on the Parse!
                    #   - respect multipart/alternative: pick one for display
                    msg['metadata'] = md
                    parsed = await CommandParse.Parse(self, msg,
                        with_nothing=True,
                        with_headers=True,
                        with_data=True,
                        with_text=True,
                        with_html=want_html,
                        with_html_clean=want_html,  # FIXME: Choose one?
                        with_html_text=True,
                        with_structure=True)

                    msg['email'] = parsed = parsed['parsed']

                    headers = {}
                    for hdr in ('Subject', 'From', 'To', 'Cc', 'Date'):
                        val = parsed.get(hdr.lower())
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
                    siblings = []
                    multipart_type = None
                    if '_PARTS' in parsed:
                        partstack = [body]
                        depth = 0
                        ignored_parts = set()
                        for i, _part in enumerate(parsed['_PARTS']):
                            content_type = _part.get('content-type', ['text/plain'])[0]
                            info = {'id': i+1, 'content-type': content_type}

                            part_id = magic_part_id(i+1, _part)
                            if part_id:
                                info['magic-id'] = part_id

                            filename = part_filename(_part)
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

                            if content_type != 'text/x-mime-postamble':
                                partstack[-1].append(info)

                            if content_type.startswith('multipart/'):
                                info['content'] = []
                                partstack.append(info['content'])
                                depth += 1

                            elif i in ignored_parts:
                                continue

                            elif '_TEXT' in _part:
                                if 'html' == content_type[-4:]:
                                    if content_type in shown_types:
                                        # FIXME: Way to select uncleaned HTML?
                                        html = _part.get('_HTML_CLEAN', _part['_TEXT'])
                                        info['content'] = html
                                    else:
                                        # We just converted this into text/plain!
                                        info['content-type'] = 'text/plain'
                                        info['content'] = _part['_HTML_TEXT']
                                else:
                                    info['content'] = _part['_TEXT']

                    result = {
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
                        '_parsed': msg}

                    if fmt in ('html', 'jhtml'):
                        func = _as_html
                    elif notmuch_compatible:
                        func = _as_notmuch_text
                    else:
                        func = _as_simple_text

                    yield (func, result)
              except APIException as e:
                # Message not found
                pass
              except Exception as e:
                logging.exception('Failed to format message')
                pass

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
            self.print_json_list_start(nl='')
            if self.options.get('--json-ui-state'):
                self.print_json(self.webui_state)
                if result:
                    self.print_json_list_comma()
        if result:
            self.print_json(result[1], nl='')
        if last:
            self.print_json_list_end()
        else:
            self.print_json_list_comma()

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
                % (to_json(self.webui_state), pre), nl='')
        if tabular:
            self.print(
                self.format_html_tr(result[1], columns=self.HTML_COLUMNS)
                .replace('"', '\\"'), nl='')
        else:
            self.print(to_json(result[0](result[1]))[1:-1], nl='')
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

    def _get_exporter(self, cls, **kwargs):
        if self.worker:
            kwargs.update({
                'src': self.terms if (self.sync_src is None) else self.sync_src,
                'dest': self.sync_dest,
                'moggie_id': self.worker.unique_app_id})
        if self.exporter is None:
            password = (self.options.get('--zip-password=') or [None])[-1]
            export_to = self.export_to
            if not export_to:
                class _wwrap:
                    def write(ws, data):
                        self.write_reply(data)
                        return len(data)
                    def flush(ws):
                        pass
                    def close(ws):
                        pass
                export_to = _wwrap()
            if password:
                kwargs['password'] = bytes(password, 'utf-8')
                self.exporter = cls(export_to, **kwargs)
                if not self.exporter.can_encrypt():
                    raise Nonsense('Encryption is unavailable')
            else:
                self.exporter = cls(export_to, **kwargs)
        return self.exporter

    def _export(self, exporter, result, first, last):
        exported = None
        if result is not None:
            try:
                func, data = result
                if hasattr(exporter, 'export_parsed'):
                    exporter.export_parsed(
                        data['_metadata'], data['_parsed'], func(data))
                else:
                    metadata = data['_metadata']
                    raw_email = base64.b64decode(data['_data'])
                    exported = exporter.export(metadata, raw_email)
            except:
                logging.exception('Export failed')
        if last:
            exporter.close()
        return exported

    async def emit_result_mbox(self, result, first=False, last=False):
        exporter = self._get_exporter(MboxExporter)
        return self._export(exporter, result, first, last)

    async def emit_result_zip(self, result, first=False, last=False):
        exporter = self._get_exporter(EmlExporter)
        return self._export(exporter, result, first, last)

    async def emit_result_maildir(self, result, first=False, last=False):
        exporter = self._get_exporter(MaildirExporter)
        return self._export(exporter, result, first, last)

    async def emit_result_mailzip(self, result, first=False, last=False):
        exporter = self._get_exporter(MaildirExporter,
            output=MaildirExporter.AS_ZIP)
        return self._export(exporter, result, first, last)

    async def emit_result_msgdirs(self, result, first=False, last=False):
        exporter = self._get_exporter(MsgdirsExporter)
        return self._export(exporter, result, first, last)

    async def emit_result_msgdzip(self, result, first=False, last=False):
        exporter = self._get_exporter(MsgdirsExporter,
            output=MaildirExporter.AS_ZIP)
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
        elif output == 'threads_metadata':
            self.write_error = lambda e: None
            return self.as_threads_metadata
        elif output == 'sync-info':
            return self.as_sync_info
        elif output == 'emails':
            self.write_error = lambda e: None
            return self.as_emails
        raise Nonsense('Unknown output format: %s' % output)

    def get_emitter(self, fmt=None):
        fmt = self.options['--format='][-1] if (fmt is None) else fmt
        if fmt == 'json':
            return self.emit_result_json
        elif fmt == 'jhtml':
            return self.emit_result_jhtml
        elif fmt == 'html':
            return self.emit_result_html
        elif fmt == 'text0':
            return self.emit_result_text0
        elif fmt == 'text':
            return self.emit_result_text
        elif fmt == 'sexp':
            return self.emit_result_sexp
        elif fmt == 'raw':
            return self.emit_result_raw
        elif fmt == 'mbox':
            return self.emit_result_mbox
        elif fmt == 'maildir':
            return self.emit_result_maildir
        elif fmt == 'mailzip':
            return self.emit_result_mailzip
        elif fmt == 'msgdirs':
            return self.emit_result_msgdirs
        elif fmt == 'msgdzip':
            return self.emit_result_msgdzip
        elif fmt == 'zip':
            return self.emit_result_zip
        raise Nonsense('Unknown output format: %s' % fmt)

    def get_query(self):
        fmt = self.options['--format='][-1]
        output = self.get_output()

        if self.mailboxes:
            valid_outputs = (
                'default', 'messages', 'threads', 'summary', 'files',
                'sync-info', 'metadata', 'threads_metadata', 'emails')
            if output not in valid_outputs:
                raise Nonsense('Need --output=X, with X one of: %s'
                    % ', '.join(valid_outputs))
            query = RequestMailbox(
                context=self.context,
                mailboxes=self.mailboxes,
                sync_src=self.sync_src,
                sync_dest=self.sync_dest,
                terms=self.terms)
        else:
            query = RequestSearch(context=self.context, terms=self.terms)

        query['username'] = self.options.get('--username=', [None])[-1]
        query['password'] = self.options.get('--password=', [None])[-1]

        if self.options.get('--offset=', [None])[-1]:
            query['skip'] = int(self.options['--offset='][-1])
        else:
            query['skip'] = 0

        entire = (self.options.get('--entire-thread=') or ['default'])[-1]
        entire = entire.lower()

        if output == 'summary':
            query['threads'] = (entire != 'false')
            query['only_ids'] = False
            self.batch = 2000
        elif output == 'threads_metadata':
            query['threads'] = (entire != 'false')
        elif output == 'threads':
            query['threads'] = (entire != 'false')
            query['only_ids'] = True
        elif output == 'sync-info':
            self.batch = None
        elif output == 'emails':
            query['threads'] = (entire not in ('false', 'default'))
        elif output in ('tags', 'tag_info'):
            query['uncooked'] = True
            query['mask_tags'] = []
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
            try:
                limit = int(self.options['--limit='][-1]) or None
            except ValueError:
                pass

        prev = None
        first = True
        async for result in self.results(query, limit, formatter):
            if prev is not None:
                try:
                    await emitter(prev, first=first)
                    first = False
                except:
                    logging.exception('Uncaught exception in search')
                    raise
            prev = result
        await emitter(prev, first=first, last=True)

    # FIXME: This needs upstreaming into a parent class...
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
        query['limit'] = min(batch, limit or batch) if batch else None
        msg = await self.repeatable_async_api_request(self.access, query)
        if 'emails' not in msg and 'results' not in msg:
            raise Nonsense('Search failed. Is the app locked?')

        self.webui_state['details'] = {
            'q': self.options['--q='],
            'qr': self.options.get('--qr=', [])}
        self.webui_state['preferences'] = self.preferences

        if 'results' in msg and not self.raw_results:
            self.raw_results = msg['results']
            for k in self.raw_results:
                if k not in ('hits', 'tags'):
                    self.webui_state['details'][k] = self.raw_results[k]

        output = self.get_output()
        if output in ('tags', 'tag_info'):
            return (msg.get('results', {}).get('tags') or {}).items()
        else:
            return msg.get('emails') or []

    async def results(self, query, limit, formatter):
        batch = (self.batch // 10) if self.batch else None
        output = self.get_output()
        while limit is None or limit > 0:
            results = await self.perform_query(query, batch, limit)
            if self.batch:
                batch = min(self.batch, int(batch * 1.2))

            count = len(results)
            if limit is not None:
                limit -= count

            for r in results:
                async for fd in formatter(r):
                    yield fd

            query['skip'] += count
            if ((count < (query['limit'] or 0))
                    or (not count)
                    or (batch is None)
                    or (output in ('tags', 'tag_info'))):
                break

        async for fd in formatter(None):
            yield fd



class CommandAddress(CommandSearch):
    """# moggie address [options] <search terms ...>

    Search for emails or threads matching the given search terms and display
    addresses related to them (senders, recipients or both).

    ### Examples

        moggie address to:bre dates:2022-09
        moggie address --output=recipients from:bre dates:2022-09
        moggie address --output=score --output=count bjarni* einars*

    ### Search options

    %(search)s

    ### Output options

    %(output)s

    When choosing output formats, multiple options can be specified at once.
    When `sender` is requested, the output will include all senders. The
    output from `recipients` includes the messages in To: and Cc: headers.
    Requesting `--output=address` will omit the names from e-mail addresses,
    `--output=count` will include a count of how often each address was
    seen and `--output=score` will include a score showing how well the
    name/address match the search terms provided.

    Output is unsorted (for performance reasons), unless `--output=count` is
    requested, as that requires buffering all results anyway. The counted sort
    order is descending by (score, count), so the best matches should be first.

    See also `moggie help search` and `moggie help how-to-search` for details
    about how to search for mail. This command should be compatible with its
    `notmuch` counterpart, so the man-page for `notmuch address` may also
    provide useful insights.
    """
    NAME = 'address'
    ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True
    OPTIONS = [[
        (None, None, 'search'),
        ('--context=',     ['default'], 'The context for scope and settings'),
        ('--q=',                    [], 'Search terms (used by web API)'),
        ('--deduplicate=', ['mailbox'], 'X=(no|mailbox|address)'),
        ('--offset=',          ['0'], 'Skip the first X results'),
        ('--limit=',            [''], 'Output at most X results'),
    ],[
        (None, None, 'output'),
        ('--format=',   ['text'], 'X=(text*|text0|json|sexp)'),
        ('--output=',         [], 'X=(sender*|recipients|address|count|score)'),
    # These are notmuch options which we currently ignore
        ('--sort=',           ['newest-first'], ''),
        ('--format-version=', [], ''),
        ('--exclude=',        ['true'], '')]]

    def __init__(self, *args, **kwargs):
        self.address_only = False
        self.result_cache = {}
        self.counts = False
        self.scores = False
        self.term_substrings = set()
        super().__init__(*args, **kwargs)

    def configure(self, args):
        args = super().configure(args)
        if not self.options['--output=']:
            self.options['--output='].append('sender')
        if (self.options['--deduplicate='][-1] == 'no'
                and 'count' in self.options['--output=']):
            raise Nonsense('Counting requires deduplication')

        terms = set([
            t.rstrip('*').lstrip('-').lstrip('+')
            for t in self.terms.split() if ':' not in t])
        if terms:
            maxlen = max(4, min(len(t) for t in terms))
            for term in terms:
                self.term_substrings.add(term + '$')
                for ln in range(max(2, len(term) - 4), len(term)-1):
                    for idx in range(0, len(term) - ln):
                        self.term_substrings.add(term[idx:min(maxlen, idx+ln)])

        return args

    def validate_configuration(self, output=True, zip_encryption=True):
        return True

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

    def calc_score(self, result):
        if not self.term_substrings:
            result['score'] = 1 if result.get('name') else 0
            return

        name_score = sum(10 if ('$' in sub) else 1
                for sub in self.term_substrings
                if sub.rstrip('$') in result['name'])
        addr_score = sum(10 if ('$' in sub) else 1
                for sub in self.term_substrings
                if sub.rstrip('$') in result['address'])

        result['score'] = name_score + addr_score

    async def emit_sender(self, md, first=False, last=False):
        if md is not None:
            addr = Metadata(*md).parsed().get('from')
            if addr and addr.address:
                both = ('%s <%s>' % (addr.fn or '', addr.address)).strip()
                result = {
                    'address': addr.address,
                    'name': addr.fn,
                    'name-addr': both}
                if self.is_new(addr, result):
                    if self.scores:
                        self.calc_score(result)
                    yield (self.fmt, result)

    async def emit_recipients(self, md, first=False, last=False):
        if md is not None:
            mdp = Metadata(*md).parsed()
            for hdr in ('to', 'cc', 'bcc'):
                for addr in mdp.get(hdr, []):
                    both = ('%s <%s>' % (addr.fn or '', addr.address)).strip()
                    result = {
                        'address': addr.address,
                        'name': addr.fn,
                        'name-addr': both}
                    if self.is_new(addr, result):
                        if self.scores:
                            self.calc_score(result)
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
            elif out == 'score':
                self.scores = True
            else:
                raise Nonsense('Unknown output type: %s' % out)
        if not formatters:
            formatters.append(self.emit_sender)

        self.fmt = '%(address)s' if self.address_only else '%(name-addr)s'
        if self.counts:
            self.fmt = '%(count)s\t' + self.fmt
        if self.scores:
            self.fmt = '%(score)s\t' + self.fmt

        async def _formatter(md):
            if md is not None:
                for _fmt in formatters:
                    async for result in _fmt(md):
                        yield result
        if not self.counts:
            return _formatter

        async def _counter(md):
            if md is None:
                key_counts = list(self.displayed.items())
                key_counts.sort(key=lambda k_c: (
                    -self.result_cache[k_c[0]].get('score', 0),
                    -k_c[1],
                    self.result_cache[k_c[0]].get('name-addr', '')))
                for key, count in key_counts:
                    r = self.result_cache[key]
                    r['count'] = count
                    yield (self.fmt, r)
            else:
                async for r in _formatter(md):
                    pass
        return _counter


class CommandShow(CommandSearch):
    """moggie show [options] <terms>

    Display messages or extract parts from e-mails or threads matching
    a given search.

    ### Search options

    %(search)s

    ### Output options

    %(output)s

    ### Examples

        ...

    FIXME
    """
    NAME = 'show'
    ROLES = None
    REAL_ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True

    RE_SIGNED_ID = re.compile(
        r'^id:[0-9a-f,]+\.[0-9a-zA-Z]+\.[0-9a-zA-Z]+\.[0-9a-f]+$')

    OPTIONS = [[
        (None, None, 'search'),
        ('--context=',     ['default'], 'The context for scope and settings'),
        ('--q=',                    [], 'Search terms (used by web API)'),
        ('--deduplicate=', ['mailbox'], 'X=(no|mailbox|address)'),
        ('--username=',         [None], 'Username with which to access email'),
        ('--password=',         [None], 'Password with which to access email'),
    ],[
        (None, None, 'output'),
        ('--format=',   ['text'], 'X=(text*|text0|json|sexp)'),
        ('--output=',         [], 'X=(sender*|recipients|address|count)'),
        ('--indent=',      [' '], 'Prefix used to indent message body (default=` `)'),
        ('--limit=',        [''], ''),
        ('--entire-thread=',  [], 'X=(true|false*), show all messages in thread?'),
        ('--body=',     ['true'], 'X=(true*|false), output message body?'),
        ('--part=',           [], 'Show part number X (0=entire raw message)'),
        ('--include-html',    [], 'Include HTML parts in output'),
    # These are notmuch options which we currently ignore
        ('--verify',          [], ''),
        ('--decrypt=',        [], ''),
        ('--format-version=', [], 'Set this to any value for notmuch-compatible output'),
        ('--exclude=',    ['true'], ''),
        ('--duplicate=',      [''], '')]]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # FIXME: This needs upstreaming into a parent class...
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

    async def results(self, query, limit, formatter):
        # This is a shortcut for API use, where we pass already cooked
        # metadata to show - this lets us avoid the search.
        metadata = None
        if isinstance(self.terms, (dict, list)):
            # Metadata as dict!
            metadata = self.terms
        elif self.terms[:1] in ('{', '[') and self.terms[-1:] in (']', '}'):
            metadata = self.terms
        if metadata:
            metadata = Metadata.FromParsed(metadata)
            async for fd in formatter(metadata):
                yield fd
        else:
            async for result in super().results(query, limit, formatter):
                yield result

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
            elif details == ['0']:
                self.mimetype = 'message/rfc822'
            else:
                self.mimetype = 'application/octet-stream'

        if not self.terms:
            raise Nonsense('Show what?')
        return args

    async def run(self):
        if self.options.get('--part='):
            self.options['--format='] = ['raw']
        if self.options['--format='][-1] in ('json', 'sexp', 'text', 'text0'):
            self.options['--entire-thread='][:0] = ['true']
        if self.options['--format='][-1] in ('html', 'jhtml'):
            if self.preferences['display_html'] == 'yes':
                self.options['--include-html'] = ['true']
        return await super().run()


class CommandCount(CLICommand):
    """moggie count [options] <terms>

    Count how many messages or threads match the given search terms.
    Multiple searches can be performed at once, and terms can be loaded
    from standard input or a file, as well as the command line.

    With no search terms, return results for the entire context.

    ### Search options

    %(search)s

    ### Output options

    %(output)s

    ### Examples

        ...

    FIXME
    """
    NAME = 'count'
    ROLES = AccessConfig.GRANT_READ
    WEBSOCKET = False
    WEB_EXPOSE = True
    OPTIONS = [[
        (None, None, 'search'),
        ('--stdin=',            [], ''),  # Internal: lots stdin hack
        ('--context=', ['default'], 'The context for scope and settings'),
        ('--q=',                [], 'Search terms (used by web API)'),
        ('--multi',             [], 'Search and count each term separately'),
    ],[
        (None, None, 'output'),
        ('--format=',     ['text'], 'X=(text*|text0|json|sexp)'),
        ('--batch',             [], 'Read terms, one per line, from stdin'),
        ('--input=',            [], 'Read terms, one per line, from file X'),
    # These are notmuch options which still need work
        ('--output=', ['messages'], ''),   # FIXME
        ('--lastmod',           [], '')]]

    def configure(self, args):
        args = self.strip_options(args)
        self.terms = []

        if self.options['--multi']:
            self.terms = args
            self.terms.extend(self.options['--q='])
        elif args:
            self.terms = [' '.join(args + self.options['--q='])]

        if not self.terms:
            self.terms = ['*']

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
        query = RequestCounts(
            context=self.context,
            terms_list=list(set(self.terms)))
        msg = await self.worker.async_api_request(self.access, query)

        if self.options['--lastmod']:
            suffix = '\tlastmod-unsupported 1'  # FIXME?
        else:
            suffix = ''

        if self.options['--format='][-1] == 'json':
            self.print_json(msg.get('counts', {}))
        elif self.options['--format='][-1] == 'sexp':
            self.print_sexp(msg.get('counts', {}))
        else:
            for term in self.terms:
                count = msg.get('counts', {}).get(term, 0)
                if self.options['--multi']:
                    self.print('%-10s\t%s' % (count, term))
                else:
                    self.print('%d%s' % (count, suffix))


class CommandReply(CommandEmail):
    """moggie reply [options] <terms>

    FIXME: Finish this?

    Generate headers and optionally a message template for replying
    to a set of messages.

    When replying to multiple messages, the headers will be constructed
    with the assumption that the most recent message is being replied to,
    but others are being quoted and referenced for context.

    Message templates are simple non-MIME plain/text messages which
    can be edited directly using a text editor. For more complex message
    structures (multipart, HTML text), applications should use the JSON
    format and construct their own message using the provided data.

    Note: This command is notmuch-compatibility command; see `moggie email`
          for a more powerful composition tool.
    """
    NAME = 'reply'
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--format=':         ['default'], # Also json, sexp, headers-only
        '--stdin=':          [],          # Allow lots to send us stdin

        '--reply=':          [],  # Search terms
        '--decrypt=':        [],  # false, auto, true  (FIXME: notmuch compat)
        '--reply-to=':       ['all'],  # all or sender (notmuch compat)
        }

    def configure2(self, args):
        self.options['--reply='].extend(self._get_terms(args))
        return []

    def render_result(self):
        self.print(self.render())


class CommandTag(CLICommand):
    """# moggie tag [options] +<tag>|-<tag> [...] -- <search terms ...>

    Tag or untag e-mails matching a particular search query.

    Alternately, add/remove metadata from the tags themselves.

    ### Examples

        moggie tag +family -- to:bjarni from:dad
        moggie tag --context=Personal -play +school -- homework

    Instead of a search query, a JSON object can be used to add or
    remove metadata on the tags themselves:

        moggie tag +family -- 'META={"name": "My Family"}'
        moggie tag +family +school -- 'META={"parent": "personal"}'

    FIXME: DELETE THIS FEATURE, CREATE tag-meta COMMAND INSTEAD.

    ### Tagging options

    %(tagging)s

    ### Tag naming conventions

    Tag names are short strings of UTF-8 characters, which should adhere
    to a few rules:

       * They should use lower-case only
       * White-space is not allowed
       * The following ASCII characters are not allowed: `@`
       * Tags generated for internal use by moggie will have the fixed
         prefix `_mp_`.

    In addition, user interfaces may make the following assumptions:

       * Tags beginning or ending with an underscore `_` should not
         be displayed to the user unless specifically requested.
       * Elsewhere in a tag-name the `_` may optionally be rendered as
         a blank space.
       * Automatically capitalizing certain letters or even entire tag
         names for aesthetic or usability reasons is allowed.

    Tags do not need to be explicitly created before use; they are
    created (or destroyed) on demand.

    FIXME: Further clarify what we mean by lower-case in a i18n context.

    ### Tag history

    Moggie keeps a history of tag operations, primarily to allow the user
    to *undo* any mistakes. Related options:

    %(history)s

    As a rule, most tag operations should include a human-readable comment
    explaining what happened. This comment is recorded in the log, along
    with the information required to undo/redo the operation itself.

    Note that tag metadata changes are not recorded in the log and cannot
    be undone.

    ### Tagging within a mailbox

    If the search includes one or more `mailbox:/` terms, then moggie's
    limited ability to search directly within mailboxes (local or remote)
    will be used instead of the default global search index. Matching
    messages will be tagged as usual, with the additional side effect of
    adding previously unseen messages to the global metadata index.

    This can be used to selectively import only certain messages from a
    larger mailbox into moggie's index. Assigning the special `incoming` tag
    will treat the message as newly discovered and standard filters (including
    junk/spam filters) will be applied:

         moggie tag +incoming -- mailbox:/path/to/mail.mbx is:unindexed

    ### Batch operations

    Moggie allows users (or apps) to apply multiple search-and-tag operations
    as a single batch. Options:

    %(batch)s

    For the purposes of recording history and facilitating undo, we treat
    all operations within a single batch as one; all searches are performed
    before any tagging takes place and which means the entire batch op can
    be undone in one step as well.

    This means a batch like this:

        +inbox +read -potatoes -- in:potatoes
        +veggies -- in:potatoes

    ... will tag all the messages as 'in:veggies', even though the first
    line strips the 'in:potatoes' tag and the second would be a no-op if
    they were done one after another.

    Batches can have in-line trailing comments using a '#' sign, but it
    must be both preceded and followed by a space: # like this.

    To remove all matches for a single search within a batch (similar to
    the `--remove-all` option for a simple invocation), use '-*' as a tag
    operation.
    """
    NAME = 'tag'
    ROLES = AccessConfig.GRANT_READ + AccessConfig.GRANT_TAG_RW
    MAX_TAGOPS = 5000
    WEB_EXPOSE = True
    OPTIONS = [[
        (None, None, 'tagging'),
        ('--context=', ['default'], 'The context for scope and settings'),
        ('--username=',     [None], 'Username with which to access email'),
        ('--password=',     [None], 'Password with which to access email'),
        ('--remove-all',    [],'First strip all tags from matching messages'),
        ('--or',       [False], 'Use OR instead of AND with search terms'),
        ('--big',           [],
               'Override sanity checks and allow large, slow operations'),
    ],[
        (None, None, 'batch'),
        ('--batch',             [], 'Read ops, one per line, from stdin'),
        ('--input=',            [], 'Read ops, one per line, from file X'),
    ],[
        (None, None, 'history'),
        ('--comment=',      [None], 'Explain this operation in the tag history'),
        ('--undo=',         [None], 'X=<id>, undo a previous tag operation'),
        ('--redo=',         [None], 'X=<id>, redo an undone tag operation'),
    ],[
        ('--format=',       [None], ''),
        ('--output=',           [], 'X=ids, Expand int-sets to ID lists'),
        ('--stdin=',            [], '')]]  # Internal: lots stdin hack

    def _batch_configure(self, ifd):
        import shlex
        for line in ifd:
            line = line.strip()
            if line and not line.startswith('#'):
                tagops, terms = line.split('--', 1)
                terms = terms.strip()
                tagops = shlex.split(tagops)
                self.validate_and_normalize_tagops(tagops)
                if terms.startswith('META={'):
                    yield (tagops, from_json(terms[5:]), None)
                else:
                    yield (tagops, terms.split(' # ')[0].strip(), None)

    def configure(self, args):
        self.mailboxes = None
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
            self.validate_and_normalize_tagops(tags)

            if not tags or not terms:
                raise Nonsense('Nothing to do?')

            mailboxes, terms = self.remove_mailbox_terms(terms)
            terms = self.combine_terms(terms)  # Respects --or
            if terms.startswith('META={'):
                terms = from_json(terms[5:])
            self.tagops = [(tags, terms, mailboxes)]

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

    def expand_intsets(self, result):
        changed = result.get('changed')
        if changed is not None:
            result['changed'] = list(dumb_decode(result['changed']))

    def as_text(self, result):
        txt = 'Tagged:\t\t%(comment)s\nChange ID:\t%(id)s' % result['history']
        if 'ids' in self.options['--output=']:
            ids = ' '.join('%d' % i for i in result['changed'])
            txt += '\nChanged:\t%s' % ids
        return txt

    async def run_batch(self, tagops):
        query = RequestTag(
            context=self.context,
            undoable=(not self.options['--redo='][-1]
                and not self.options['--undo='][-1]
                and self.desc),
            tag_undo_id=self.options['--undo='][-1],
            tag_redo_id=self.options['--redo='][-1],
            tag_ops=tagops,
            username=self.options['--username='][-1],
            password=self.options['--password='][-1])
        msg = await self.worker.async_api_request(self.access, query)

        fmt = self.options['--format='][-1]

        if 'ids' in self.options['--output=']:
            self.expand_intsets(msg['results'])

        if fmt == 'json':
            self.print_json(msg['results'])
        elif fmt == 'jhtml':
            self.print_jhtml(msg['results'])
        elif fmt == 'html':
            self.print_html(msg['results'])
        elif fmt == 'sexp':
            self.print_sexp(msg['results'])
        elif 'history' in msg['results']:
            self.print(self.as_text(msg['results']))
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
