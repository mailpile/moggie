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
from ...storage.exporters.mbox import MboxExporter
from ...storage.exporters.maildir import MaildirExporter, EmlExporter
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

    Options:
      --context=<ctx>   Choose which context to search within.
      --format=<fmt>    Result format: text, text0, json, zip, maildir, mbox
      --output=<data>   Result output: summary, threads, messages, files,
                                       tags, emails, thread_emails.
      --offset=<N>      Skip the first N results
      --limit=<N>       Output at most N results
      --sort=<N>        Either newest-first (the default) or oldest-first.

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
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--q=':              [],
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
        self.exporter = None
        super().__init__(*args, **kwargs)

    def configure(self, args):
        self.batch = 10000

        # Allow both --q=.. and unmarked query terms. The --q=
        # option is mostly for use with the web-CLI.
        terms = self.strip_options(args)
        terms.extend(self.options['--q='])
        self.terms = ' '.join(terms)

        if self.options['--format='][-1] == 'json':
            self.mimetype = 'application/json'
        elif self.options['--format='][-1] == 'mbox':
            self.mimetype = 'application/mbox'
        elif self.options['--format='][-1] == 'zip':
            self.mimetype = 'application/mbox'
        elif self.options['--format='][-1] == 'maildir':
            self.mimetype = 'application/x-tgz'

        if self.options['--format='][-1] in ('maildir', 'zip', 'mbox'):
            self.default_output = 'emails'

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
            info['_tag_list'] = ' (%s)' % (' '.join(tags)) if tags else ''
            info['_file_count'] = '(%d)' % fc if (fc > len(msgs)) else ''
            info['_thread'] = thread
            yield (
                'thread:%(thread)s %(date_relative)s'
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

    async def as_emails(self, thread):
        def _as_text(r):
            headers = '\n'.join('%s: %s' % (h,v) for h, v in r['headers'].items())
            def _parts(p):
                p['_ct'] = p.get('content-type', 'text/plain')
                if 'content' in p:
                    if isinstance(p['content'], list):
                        p['content'] = '\n'.join(_parts(sp) for sp in p['content'])
                else:
                    p['content'] = ('Non-text part: ' + p['_ct'])
                p['_fn'] = ''
                if 'filename' in p:
                    p['_fn'] = 'Filename: %s, ' % p['filename']
                return ("""\
\x0cpart{ ID: %(id)s, %(_fn)sContent-type: %(_ct)s
%(content)s
\x0cpart}""" % p)
            return ("""\
\x0cmessage{ id:%(i)s depth:%(d)d match:%(m)d excluded:%(e)s filename:%(f)s
\x0cheader{
%(a)s (%(r)s) (%(t)s)
%(h)s
\x0cheader}
\x0cbody{
%(b)s
\x0cbody}
\x0cmessage}""") % {
                'd': r['_depth'],
                'i': r['id'],
                'm': 1 if r['match'] else 0,
                'e': 1 if r['excluded'] else 0,
                'f': r['filename'][0] if r['filename'] else '',
                'a': r['headers'].get('From', '(unknown)'),
                'r': r['date_relative'],
                't': ' '.join(r['tags']),
                'h': headers,
                'b': '\n'.join(_parts(sp) for sp in r['body'])}
        if thread is not None:
            fmt = self.options['--format='][-1]
            part = int((self.options.get('--part=') or [0])[-1])
            raw = (fmt in ('mbox', 'maildir', 'raw', 'zip'))
            want_body = raw or (self.options['--body='][-1] != 'false')
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
                    part = msg['email']['_PARTS'][part-1]
                    yield (part.get('_TEXT'),
                        {'_metadata': md, '_data': part.get('_DATA')})

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
                        for i, part in enumerate(msg['email']['_PARTS']):
                            info = {
                                'id': i+1,
                                'content-type': part.get('content-type', ['text/plain'])[0]}

                            disp = part.get('content-disposition')
                            if isinstance(disp, list):
                                info['content-disposition'] = disp[0]
                                if 'filename' in disp[1]:
                                    info['filename'] = disp[1]['filename']
                                cte = part.get('content-transfer-encoding')
                                if cte:
                                    info['content-transfer-encoding'] = cte
                                info['content-length'] = (part['_BYTES'][2] - part['_BYTES'][1])
                            while part['_DEPTH'] < depth:
                                partstack.pop(-1)
                                depth -= 1
                            if info['content-type'] != 'text/x-mime-postamble':
                                partstack[-1].append(info)
                            if '_TEXT' in part and info['content-type'] in shown_types:
                                info['content'] = part['_TEXT']
                            elif info['content-type'].startswith('multipart/'):
                                info['content'] = []
                                partstack.append(info['content'])
                                depth += 1

                    yield (_as_text, {
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
        self.print(''.join([
            '[' if first else ' ',
            json.dumps(result[1]) if result else '',
            ']' if last else ',']))

    def _get_exporter(self, cls):
        if self.exporter is None:
            class _wwrap:
                def write(ws, data):
                    self.write_reply(data)
                    return len(data)
                def flush(ws):
                    pass
                def close(ws):
                    pass
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

    def get_formatter(self):
        output = (self.options['--output='] or ['default'])[-1]
        if output == 'default':
            output = self.default_output
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
        elif fmt == 'text0':
            return self.emit_result_text0
        elif fmt in 'text':
            return self.emit_result_text
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
        if self.terms.startswith('mailbox:'):
            valid_outputs = ('default', 'threads', 'summary', 'metadata',
                             'files', 'emails')
            if self.options['--output='][-1] not in valid_outputs:
                raise Nonsense('Need --output=X, with X one of: %s'
                    % ', '.join(valid_outputs))
            query = RequestMailbox(
                context=self.context,
                mailbox=self.terms[8:])
        else:
            query = RequestSearch(context=self.context, terms=self.terms)

        if self.options.get('--offset=', [None])[-1]:
            query['skip'] = int(self.options['--offset='][-1])
        else:
            query['skip'] = 0

        output = self.options['--output='][-1]
        if output == 'default':
            output = self.default_output
        if output == 'summary':
            query['threads'] = True
            query['only_ids'] = False
            self.batch = 2000
        elif output == 'threads':
            query['threads'] = True
            query['only_ids'] = True
        elif output == 'emails':
            if (self.options.get('--entire-thread=') or ['false'])[-1] != 'false':
                query['threads'] = True
        elif output in ('tags', 'tag_info'):
            query['uncooked'] = True
            if query['skip'] or self.options.get('--limit=', [None])[-1]:
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

        output = self.options['--output='][-1]
        if output in ('tags', 'tag_info'):
            return (msg.get('results', {}).get('tags') or {}).items()
        else:
            return msg.get('emails') or []

    async def results(self, query, limit, formatter):
        batch = self.batch // 10
        output = self.options['--output='][-1]
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
    NAME = 'address'
    ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--source=':         [],
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

    def emit_sender(self, md):
        addr = Metadata(*md).parsed().get('from')
        result = {
            'address': addr.address,
            'name': addr.fn,
            'name-addr': '%s <%s>' % (addr.fn, addr.address)}
        if addr and self.is_new(addr, result):
            yield (self.fmt, result)

    def emit_recipients(self, md):
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
            elif out == 'address':
                self.address_only = True
            elif out == 'count':
                self.counts = True
            else:
                raise Nonsense('Unknown output type: %s' % out)
        if not formatters:
            formatters.append(self.emit_sender)

        self.fmt = '%(address)s' if self.address_only else '%(name-addr)s'
        def _formatter(md):
            if md is not None:
                for _fmt in formatters:
                    yield from _fmt(md)
        if not self.counts:
            return _formatter

        self.fmt = '%(count)s\t' + self.fmt
        def _counter(md):
            if md is None:
                for key, count in self.displayed.items():
                    r = self.result_cache[key]
                    r['count'] = count
                    yield (self.fmt, r)
            else:
                for r in _formatter(md):
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

    async def emit_result_json(self, result, first=False, last=False):
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

            self.print(json.dumps(threads))

    def configure(self, *args, **kwargs):
        args = super().configure(*args, **kwargs)
        self.options['--output='] = ['emails']
        if not self.terms:
            raise Nonsense('Show what?')
        return args

    async def run(self):
        if self.options.get('--part='):
            self.options['--format='] = ['raw']
        if self.options['--format='][-1] in ('json', 'sexp'):
            self.options['--entire-thread='][:0] = ['true']
        return await super().run()


class CommandCount(CLICommand):
    NAME = 'count'
    ROLES = AccessConfig.GRANT_READ
    WEB_EXPOSE = True
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        '--multi':           [],          # Multiple terms as arguments?
        '--format=':         ['text'],    # Also json!
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
            if self.options['--input=']:
                for fn in self.options['--input=']:
                    if fn == '-':
                        self.terms.extend(ln.strip() for ln in sys.stdin)
                    else:
                        self.terms.extend(ln.strip() for ln in open(fn, 'r'))
            else:
                self.terms.extend(ln.strip() for ln in sys.stdin)

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
            self.print(json.dumps(msg['counts']))
        else:
            for term in self.terms:
                count = msg.get('counts', {}).get(term, 0)
                if self.options['--multi']:
                    self.print('%-10s\t%s' % (count, term))
                else:
                    self.print('%d%s' % (count, suffix))


class CommandTag(CLICommand):
    """# moggie tag [options] +<tag>|-<tag> [...] -- <search terms ...>

        # FIXME: We are going to treat multiple batch ops as a single tag
        #        op, which effects messages all at once and can be undone
        #        all at once as well.
        #
        #        This means a batch like so:
        #           +inbox -unread -incoming -- in:incoming
        #           +potato -- in:incoming
        #
        #        Will tag all the messages as 'in:potato', even though
        #        the first line would otherwise untag them and the second
        #        would be a no-op if they were done one after another.

        # In addition to --remove-all, we should allow a -* tag op which
        # removes all tags from matching messages. This will let us use
        # remove-all behavior selectively within a batch.
        #
        # Is this notmuch compatible? Do I care? Should I ask? Test?

    """
    NAME = 'tag'
    ROLES = AccessConfig.GRANT_READ + AccessConfig.GRANT_TAG_RW
    WEB_EXPOSE = True
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        # These are notmuch options which we implement
        '--remove-all':      [],
        '--batch':           [],
        '--input=':          []}

    def _validate_and_normalize_tagops(self, tagops):
        for idx, tagop in enumerate(tagops):
            # FIXME: Undo the %-encoding of the tag name
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
            tagops[idx] = tagop.lower()

    def _batch_configure(self, ifd):
        for line in ifd:
            line = line.strip()
            if line and not line.startswith('#'):
                tagops, terms = line.split('--')
                tagops = tagops.strip().split()
                self._validate_and_normalize_tagops(tagops)
                yield (tagops, terms.strip())

    def configure(self, args):
        self.tagops = []
        self.desc = 'tag %s' % ' '.join(args)
        if '--' in args:
            ofs = args.index('--')
            tags = self.strip_options(args[:ofs])
            terms = args[ofs+1:]
        else:
            tags, terms = self.strip_options(args), []

        if self.options['--batch'] and not self.options['--input=']:
            self.options['--input='].append('-')
        for fn in set(self.options['--input=']):
            if fn == '-':
                self.tagops.extend(self._batch_configure(sys.stdin))
            else:
                with open(fn, 'r') as fd:
                    self.tagops.extend(self._batch_configure(fd))

        if not self.options['--input=']:
            while tags and tags[-1][:1] not in ('+', '-'):
                terms[:0] = [tags.pop(-1)]
            self._validate_and_normalize_tagops(tags)

            if not tags or not terms:
                raise Nonsense('Nothing to do?')

            self.tagops = [(tags, ' '.join(terms))]
        elif tags or terms:
            raise Nonsense('Use batches or the command line, not both')

        return []

    async def run(self):
        from ...jmap.requests import RequestTag

        query = RequestTag(
            context=self.context,
            undoable=self.desc,
            tag_ops=self.tagops)
        msg = await self.worker.async_jmap(self.access, query)
        self.print('%s' % msg)


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
