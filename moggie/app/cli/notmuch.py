# These are CLI commands which aim to behave as similarly to notmuch as
# possible. Because why not? Compatibility is nice.
#
# FIXME: Most of the complex logic in here should probably be moved to the
#        back-end, so we can expose the same API via the web.
#
import copy
import datetime
import json
import sys

from .command import Nonsense, CLICommand
from ...email.metadata import Metadata


class CommandSearch(CLICommand):
    """# moggie search [options] <search terms ...>

    Search for emails or threads matching the given search terms. Search
    terms are exact matches, unless the wildcard (*) is used. Examples:

        moggie search bjarni    # Exact match
        moggie search bjarn*    # Will match bjarni or bjarna

    """
    NAME = 'search'
    WEBSOCKET = False
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
        # These are notmuch options which we implement
        '--format=':         ['text'],
        '--offset=':         ['0'],
        '--limit=':          [''],
        # These are notmuch options which we currently ignore
        '--output=':         ['summary'],
        '--sort=':           ['newest-first'],
        '--format-version=': [''],
        '--exclude=':        ['true'],
        '--duplicate=':      ['']}

    def __init__(self, *args, **kwargs):
        self.displayed = {}
        super().__init__(*args, **kwargs)

    def configure(self, args):
        self.batch = 10000
        self.terms = ' '.join(self.strip_options(args))
        if self.terms == '*':
            self.terms = 'all:mail'
        if self.options['--format='][-1] == 'json':
            self.mimetype = 'application/json'
        return []

    def as_metadata(self, md):
        if md is not None:
            yield ('%s', Metadata(*md).parsed())

    def as_threads(self, thread):
        if thread is not None:
            yield ('%s', 'thread:%8.8d' % thread['thread'])

    def as_summary(self, thread):
        if thread is not None and thread['hits']:
            msgs = dict((i[1], Metadata(*i).parsed())
                for i in thread['messages'])

            top = msgs[thread['thread']]
            md = msgs[thread['hits'][0]]

            ts = min(msgs[i]['ts'] for i in thread['hits'])
            dt = datetime.datetime.fromtimestamp(ts)
            fc = sum(len(m['ptrs']) for m in msgs.values())
            date = '%4.4d-%2.2d-%2.2d' % (dt.year, dt.month, dt.day)
            tags = []
            authors = ', '.join(list(set(
                (m['from']['fn'] or m['from']['address'])
                for m in msgs.values() if 'from' in m)))
            info = {
                'thread': '%8.8d' % thread['thread'],
                'timestamp': ts,
                'date_relative': date,
                'matched': len(thread['hits']),
                'total': len(msgs),
                'files': fc,
                'authors': authors,
                'subject': top.get('subject', md.get('subject', '(no subject)')),
                'tags': tags}
            info['_tag_list'] = ' (%s)' % (' '.join(tags)) if tags else ''
            info['_file_count'] = '(%d)' % fc if (fc > len(msgs)) else ''
            yield (
                'thread:%(thread)s %(date_relative)s'
                ' [%(matched)s/%(total)s%(_file_count)s]'
                ' %(authors)s;'
                ' %(subject)s%(_tag_list)s',
                info)

    def as_messages(self, md):
        if md is not None:
            md = Metadata(*md)
            yield ('%s', 'id:%8.8d' % md.idx)

    def as_tags(self, md):
        if md is not None:
            if False:
                yield ('%s', Metadata(*md).thread_id)

    def as_files(self, md):
        from ...util.dumbcode import dumb_decode
        # FIXME: File paths are BINARY BLOBS. We need to output them as
        #        such, if possible. Especially in text mode!
        if md is not None:
            md = Metadata(*md)
            for p in md.pointers:
                try:
                    fn = str(dumb_decode(p.ptr_path), 'utf-8')
                except UnicodeDecodeError:
                    fn = str(dumb_decode(p.ptr_path), 'latin-1')
                yield ('%s', fn)

    async def emit_result_text(self, result, first=False, last=False):
        if result is not None:
             self.print(result[0] % result[1])

    async def emit_result_text0(self, result, first=False, last=False):
        if result is not None:
            self.write_reply(result[0] % result[1])
            self.write_reply('\0')

    async def emit_result_json(self, result, first=False, last=False):
        if result:
            result, keys = copy.copy(result[1]), result[1].keys()
            for k in keys:
                if k[:1] == '_':
                    del result[k]
        self.print(''.join([
            '[' if first else ' ',
            json.dumps(result) if result else '',
            ']' if last else ',']))

    def get_formatter(self):
        output = (self.options['--output='] or ['summary'])[-1]
        if output == 'summary':
            return self.as_summary
        elif output == 'threads':
            return self.as_threads
        elif output == 'messages':
            return self.as_messages
        elif output == 'tags':
            return self.as_tags
        elif output == 'files':
            return self.as_files
        elif output == 'metadata':
            return self.as_metadata
        raise Nonsense('Unknown output format: %s' % fmt)

    def get_emitter(self):
        fmt = self.options['--format='][-1]
        if fmt == 'json':
            return self.emit_result_json
        elif fmt == 'text0':
            return self.emit_result_text0
        elif fmt == 'text':
            return self.emit_result_text
        raise Nonsense('Unknown output format: %s' % fmt)

    def get_query(self):
        from ...jmap.requests import RequestSearch

        query = RequestSearch(
            context=self.context(),
            terms=self.terms)

        if self.options.get('--offset=', [None])[-1]:
            query['skip'] = int(self.options['--offset='][-1])
        else:
            query['skip'] = 0

        if self.options['--output='][-1] == 'summary':
            query['threads'] = True
            query['only_ids'] = False
            self.batch = 2000
        elif self.options['--output='][-1] == 'threads':
            query['threads'] = True
            query['only_ids'] = True

        return query

    async def run(self):
        from ...config import AppConfig

        formatter = self.get_formatter()
        emitter = self.get_emitter()
        query = self.get_query()

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

    async def perform_query(self, query, batch, limit):
        query['limit'] = min(batch, limit or batch)
        msg = await self.worker.async_jmap(query)
        if 'emails' not in msg:
            raise Nonsense('Search failed. Is the app locked?')

        return msg.get('emails') or []

    async def results(self, query, limit, formatter):
        batch = self.batch // 10
        while limit is None or limit > 0:
            results = await self.perform_query(query, batch, limit)
            batch = min(self.batch, int(batch * 1.2))
            count = len(results)
            if limit is not None:
                limit -= count

            for r in results:
                for fd in formatter(r):
                    yield fd

            query['skip'] += count
            if count < query['limit'] or not count:
                break

        for fd in formatter(None):
            yield fd


class CommandAddress(CommandSearch):
    NAME = 'address'
    OPTIONS = {
        # These are moggie specific
        '--context=':        ['default'],
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


class CommandCount(CLICommand):
    NAME = 'count'
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
            context=self.context(),
            terms_list=list(set(self.terms)))
        msg = await self.worker.async_jmap(query)

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
    NAME = 'tag'


def CommandConfig(wd, args):
    from ...config import AppConfig
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
