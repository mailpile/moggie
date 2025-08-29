import asyncio
import base64
import copy
import logging
import os
import time
import sys

from moggie import get_shared_moggie

from ... import config
from ...api.exceptions import *
from ...api.requests import *
from ...config import AccessConfig
from ...util.dumbcode import to_json, from_json
from ...util.mailpile import tag_unquote

from .exceptions import *


class CLICommand:
    AUTO_START = False
    NAME = 'command'
    ROLES = AccessConfig.GRANT_ALL
    OPTIONS = []
    CONNECT = True
    WEBSOCKET = True

    HTML_HEADER = """\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>%(title)s - moggie</title>
 <link rel=stylesheet href="/themed/css/webui.css?v=%(version)s">
 <link rel=stylesheet href="/themed/css/%(command)s.css?v=%(version)s">
 <script language=javascript>moggie_state = %(state)s;</script>
 <script language=javascript src='/static/js/moggie_api.js?v=%(version)s' defer></script>
 <script language=javascript src='/static/js/webui.js?v=%(version)s' defer></script>
</head><body><div class="content">
"""
    HTML_FOOTER = """
</div></body><!-- version=%(version)s --></html>"""

    @classmethod
    async def WebRunnable(cls, app, access, frame, conn, req_env, args):
        def reply(msg, eof=False):
            if msg or eof:
                if isinstance(msg, (bytes, bytearray)):
                    return conn.sync_reply(frame, msg, eof=eof)
                else:
                    return conn.sync_reply(frame, bytes(msg, 'utf-8'), eof=eof)
        try:
            cmd_obj = cls(get_shared_moggie(), args,
                access=access, appworker=app, connect=False, req_env=req_env)
            cmd_obj.write_reply = reply
            cmd_obj.write_error = reply
            return cmd_obj
        except PermissionError:
            logging.exception('Failed %s' % cls.NAME)
            raise
        except:
            logging.exception('Failed %s' % cls.NAME)
            reply('', eof=True)

    @classmethod
    async def MsgRunnable(cls, moggie, args):
        reply_buffers = [[]]
        def reply(msg, eof=False):
            if msg or eof:
                if isinstance(msg, (bytes, bytearray)):
                    reply_buffers[-1].append(bytes(msg))
                elif isinstance(msg, str):
                    reply_buffers[-1].append(bytes(msg, 'utf-8'))
                else:
                    reply_buffers[-1].append(msg)
        try:
            cmd_obj = cls(moggie, copy.copy(args),
                access=moggie._access,
                appworker=moggie._app_worker,
                connect=False)
            cmd_obj.set_msg_defaults(copy.copy(args))
            cmd_obj.reply_buffers = reply_buffers
            cmd_obj.write_reply = reply
            cmd_obj.write_error = reply
            cmd_obj.stdin = []
            return reply_buffers[0], cmd_obj
        except PermissionError:
            logging.exception('Failed %s' % cls.NAME)
            raise
        except:
            logging.exception('Failed %s' % cls.NAME)
        return None

    def __init__(self, moggie, args,
            access=None, appworker=None, connect=True, req_env=None):
        from ...workers.app import AppWorker
        from ...util.rpc import AsyncRPCBridge

        self.options = {}
        for opt_group in self.OPTIONS:
            self.options.update(dict((opt, copy.copy(ini))
                for (opt, ini, comment) in opt_group if opt))

        self.connected = False
        self.messages = []
        self.moggie = moggie
        self.workdir = moggie.work_dir
        self.context = None
        self.preferences = None
        self.stdin = sys.stdin.buffer
        self.tempfiles = []
        self.cfg = (appworker and appworker.app and appworker.app.config)

        if access is not True and not access and self.ROLES:
            raise PermissionError('Access denied')
        self.access = access
        if self.ROLES and '--context=' not in self.options:
            self.context = self.get_context('default')

        def _writer(stuff):
            if isinstance(stuff, str):
                return sys.stdout.write(stuff)
            else:
                return sys.stdout.buffer.write(stuff)
        self.write_reply = _writer
        self.write_error = _writer
        self.skip_json = False
        self.reply_buffers = []

        self.filename = None
        self.disposition = None
        self.mimetype = 'text/plain; charset=utf-8'
        self.webui_state = {'command': self.NAME}
        if req_env is not None:
            self.set_web_defaults(req_env)

        if connect and self.CONNECT:
            self.worker = None
            self.connect(self.configure(args))
        else:
            self.configure(args)
            self.worker = appworker

        self.app = None
        self.ev_loop = asyncio.get_event_loop()
        if connect and self.WEBSOCKET:
            self.app = AsyncRPCBridge(self.ev_loop, 'cli', self.worker, self)
            if not self.ev_loop.is_running():
                self.ev_loop.run_until_complete(self._await_connection())

    async def async_ready(self):
        if self.WEBSOCKET and self.app is not None:
            await self._await_connection()

    def get_tempfile(self):
        import tempfile
        self.tempfiles.append(tempfile.NamedTemporaryFile())
        return self.tempfiles[-1]

    def remove_mailbox_terms(self, terms):
        mailboxes, terms = (
            [t[8:] for t in terms if t[:8] == 'mailbox:'] or None,
            [t for t in terms if t[:8] != 'mailbox:'])

        if not mailboxes and (len(terms) == 1):
            if terms[0][:1] in ('.', '/') and os.path.exists(terms[0]):
                mailboxes = [os.path.abspath(terms[0])]
                terms = []
            elif terms[0].startswith('imap:/') or (terms[0] == '-'):
                mailboxes = [terms[0]]
                terms = []

        return mailboxes, terms

    @classmethod
    def is_yes(self, opt):
        yn = opt[-1] if (opt and isinstance(opt, list)) else opt
        if isinstance(yn, str):
            return yn.lower() in ('y', 'yes', 't', 'true', 'on', '1')
        return bool(yn)

    @classmethod
    def is_no(self, opt):
        yn = opt[-1] if (opt and isinstance(opt, list)) else opt
        if isinstance(yn, str):
            return yn.lower() in ('n', 'no', 'f', 'false', 'off', '0')
        if isinstance(yn, bool):
            return (not yn)
        return True if (yn is None) else False

    def combine_terms(self, terms):
        if self.options.get('--or', [False])[-1]:
            return ' OR '.join('(%s)' % term for term in terms)
        else:
            return ' '.join(terms)

    def connect(self, args=[]):
        if not self.worker:
            from ...workers.app import AppWorker
            self.worker = AppWorker.FromArgs(self.workdir, args)
            if not self.worker.connect(autostart=self.AUTO_START, quick=True):
                raise NotRunning('Failed to launch or connect to app')
        return self.worker

    def set_msg_defaults(self, args):
        self.stdin = []
        if '--format=' in self.options:
            # If the caller hasn't explicitly requested a data format, we
            # set the default to "json", but skip actually encoding it to
            # avoid duplicate effort. The print_json method will set a
            # special mime-type so our caller knows what to expect.
            fmt_args = sum(1 for a in args if a.startswith('--format='))
            if not fmt_args:
                self.skip_json = True
                self.options['--format='][:1] = ['json']

    def set_web_defaults(self, req_env):
        self.stdin = []
        if '--format=' in self.options:
            ua = (req_env.http_headers.get('User-Agent')
                or req_env.http_headers.get('user-agent')
                or '')
            at = req_env.http_headers.get('Accept') or ''

            if at.startswith('text/plain'):
                self.options['--format='][:1] = ['text']
            elif at.startswith('text/html'):
                self.options['--format='][:1] = ['html']
            elif ('json' in at) or ('Mozilla' not in ua):
                self.options['--format='][:1] = ['json']

    def print_sexp(self, data, nl='\n'):
        def _sexp(exp):
            out = ''
            if isinstance(exp, (list, tuple)):
                out += '(' + ' '.join(_sexp(x) for x in exp) + ')'
            elif isinstance(exp, dict):
                out += ('(' + ' '.join(
                        ':%s %s' % (k, _sexp(v)) for k, v in exp.items())
                    + ')')
            elif isinstance(exp, str):
                out += '"%s"' % repr(exp)[1:-1].replace('"', '\"')
            elif exp in (False, None):
                out += 'nil'
            elif exp is True:
                out += 't'
            else:
                out += '%s' % exp
            return out
        self.write_reply(_sexp(data) + nl)

    def print_html_start(self, html='', title=None, state=None):
        self.write_reply((self.HTML_HEADER % {
            'title': title or self.NAME,
            'version': config.CACHE_VERSION,
            'command': self.NAME,
            'state': to_json(state or self.webui_state)}) + html)

    def print_html_end(self, html=''):
        self.print(html + (self.HTML_FOOTER % {
            'version': config.CACHE_VERSION}))

    def print_html_tr(self, row, columns=None):
        self.write_reply(self.format_html_tr(row, columns))

    def format_html_tr(self, row, columns=None):
        def _esc(data):
            if isinstance(data, list):
                return ' '.join(_esc(d) for d in data if d)
            elif isinstance(data, int):
                return '%d' % data
            elif isinstance(data, float):
                return '%.3f' % data
            elif isinstance(data, str):
                return (data
                    .replace('&', '&amp;')
                    .replace('<', '&lt;')
                    .replace('>', '&gt;'))
            else:
                return ''

        def _link(k, text):
            url = row.get('_url_' + k)
            if url:
                return '<a href="%s">%s</a>' % (url, text)
            else:
                return text

        columns = columns or row.keys()
        return ('<tr>%s</tr>' % ''.join(
            '<td class=%s>%s</td>' % (k, _link(k, _esc(row[k])))
            for k in columns if k in row))

    def print_json_list_start(self, nl='\n'):
        if self.skip_json:
            new_list = []
            self.reply_buffers[-1].append(new_list)
            self.reply_buffers.append(new_list)
        else:
            self.write_reply('[' + nl)

    def print_json_list_comma(self, nl='\n'):
        if not self.skip_json:
            self.write_reply(',' + nl)

    def print_json_list_end(self, nl='\n'):
        if self.skip_json:
            if len(self.reply_buffers) > 1:
                self.reply_buffers.pop(-1)
                # Strip away the extra outermost 1-element list, in the case
                # where the command is incrementally emitting its own list.
                reply_buffer = self.reply_buffers[0]
                if ((len(self.reply_buffers) == 1) and
                        (len(reply_buffer) == 1) and
                        (isinstance(reply_buffer[0], list))):
                    reply_buffer[:] = reply_buffer[0]
        else:
            self.write_reply(']' + nl)

    def print_json(self, data, nl='\n'):
        if self.skip_json:
            self.mimetype = 'application/moggie-internal'
            return self.write_reply(data)
        self.mimetype = 'application/json; charset=utf-8'
        self.write_reply(to_json(data) + nl)

    def print(self, *args, nl='\n'):
        self.write_reply(' '.join(args) + nl)

    def error(self, *args, nl='\n'):
        self.write_error(' '.join(args) + nl)

    async def repeatable_async_api_request(self, access, query):
        if self.stdin in (sys.stdin, sys.stdin.buffer):
            nei = None
            try:
                return await self.worker.async_api_request(access, query)
            except NeedInfoException as exc:
                nei = exc

            from getpass import getpass
            sys.stderr.write(str(nei) + '\n')
            for need in nei.need:
                if need.datatype == 'password':
                    query[need.field] = getpass(need.label + ': ')
                elif need.datatype == 'text':
                    query[need.field] = input(need.label + ': ')

        return await self.worker.async_api_request(access, query)

    async def _await_connection(self):
        sleeptime, deadline = 0, (time.time() + 10)
        while time.time() < deadline:
            sleeptime = min(sleeptime + 0.01, 0.1)
            await asyncio.sleep(sleeptime)
            if self.connected:
                break

    def link_bridge(self, bridge):
        def _receive_message(bridge_name, raw_message):
            message = from_json(raw_message)
            if message.get('connected'):
                self.connected = True
            else:
                self.handle_message(message)
        return _receive_message

    def handle_message(self, message):
        self.messages.append(message)

    def get_all_contexts(self):
        # FIXME: This should make an API call, not load the config directly
        cfg = self.cfg
        if cfg is None:
            from ...config import AppConfig
            cfg = self.cfg = AppConfig(self.workdir)
        return self.cfg.contexts

    def get_context(self, ctx=None):
        all_contexts = self.get_all_contexts()  # Sets self.cfg as side effect
        cfg = self.cfg
        ctx = ctx or (self.options.get('--context=') or ['default'])[-1]
        if ctx == 'default':
            if self.access is True or not self.access:
                ctx = cfg.get(cfg.GENERAL, 'default_cli_context',
                    fallback='Context 0')
            else:
                ctx = self.access.get_default_context()
        else:
            if ctx not in all_contexts:
                for ctx_key, ctx_info in all_contexts.items():
                    if ctx == ctx_info.name:
                        ctx = ctx_key
                        break
        if ctx not in all_contexts:
            raise Nonsense('Invalid context: %s' % ctx)

        if self.ROLES and self.access is not True:
            if not self.access.grants(ctx, self.ROLES):
                logging.error('Access denied, need %s on %s' % (self.ROLES, ctx))
                raise PermissionError('Access denied')

        return ctx

    def metadata_worker(self):
        from ...workers.metadata import MetadataWorker
        return MetadataWorker.Connect(self.worker.worker_dir)

    def search_worker(self):
        from ...workers.search import SearchWorker
        return SearchWorker.Connect(self.worker.worker_dir)

    def strip_options(self, args, options=None):
        # This should be compatible-ish with how notmuch does things.
        leftovers = []
        auto_context = (options is None)
        options = self.options if (options is None) else options
        def _setopt(name, val):
            if name not in options:
                options[name] = []
            options[name].append(val)
        while args:
            arg = args.pop(0).strip('\n')
            if arg == '--':
                leftovers.extend(args)
                break
            elif arg in options:
                _setopt(arg, True)
            elif arg+'=' in options:
                if args and args[0][:2] != '--':
                    _setopt(arg+'=', args.pop(0).strip('\n'))
                else:
                    _setopt(arg+'=', True)
            elif arg[:2] == '--':
                if '=' in arg:
                    arg, opt = arg.split('=', 1)
                else:
                    try:
                        arg, opt = arg.split(':', 1)
                    except ValueError:
                        pass
                if arg+'=' not in options:
                    raise Nonsense('Unrecognized argument: %s' % arg)
                _setopt(arg+'=', opt)
            else:
                leftovers.append(arg)
        if auto_context and ('--context=' in self.options):
            self.context = self.get_context()
        return leftovers

    @classmethod
    def read_file_or_stdin(cls, cli_obj, path, _bytes=False):
        if path == '-':
            if cli_obj.options['--stdin=']:
                if _bytes:
                    return bytes(cli_obj.options['--stdin='].pop(0), 'utf-8')
                return cli_obj.options['--stdin='].pop(0)
            else:
                if _bytes:
                    return sys.stdin.buffer.read()
                return str(sys.stdin.buffer.read(), 'utf-8')
        else:
            with open(path, 'rb' if _bytes else 'r') as fd:
                return fd.read()

    def validate_and_normalize_tagops(self, tagops):
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
        if self.options.get('--remove-all') and '-*' not in tagops:
            tagops.insert(0, '-*')

    async def gather_emails(self,
            mailbox_and_search_lists,
            with_text=False,
            with_data=False,
            with_full_raw=False,
            with_missing=False,
            decode_raw=False,
            username=None,
            password=None):

        if username is None:
            username = self.options.get('--username=', [None])[-1]
        if password is None:
            password = self.options.get('--password=', [None])[-1]

        for mailboxes, search in mailbox_and_search_lists:
          for mailbox in (mailboxes or [None]):
            worker = self.connect()

            metadata = None
            if isinstance(search, (dict, list)):
                # Metadata as dict!
                metadata = search
            elif search[:1] in ('{', '[') and search[-1:] in (']', '}'):
                metadata = search

            if metadata:
                result = {'emails': [Metadata.FromParsed(metadata)]}
            else:
                if mailbox:
                    request = RequestMailbox(
                        context=self.context,
                        mailboxes=[mailbox],
                        username=username,
                        password=password,
                        terms=search)
                else:
                    request = RequestSearch(context=self.context, terms=search)
                result = await self.repeatable_async_api_request(
                    self.access, request)

            if result and result.get('emails'):
                for metadata in result['emails']:
                    msg = None
                    req = RequestEmail(
                            metadata=metadata,
                            text=with_text,
                            data=with_data,
                            full_raw=(with_full_raw or decode_raw),
                            username=username,
                            password=password)
                    md = Metadata(*metadata)
                    try:
                        msg = await worker.async_api_request(self.access, req)
                    except APIException:
                        if not with_missing:
                            raise
                    if msg and 'email' in msg:
                        r = {
                            'email': msg['email'],
                            'metadata': md,
                            'mailbox': mailbox,
                            'search': search}
                        if decode_raw:
                            r['data'] = base64.b64decode(msg['email']['_RAW'])
                        yield r
                    else:
                        yield {
                            'error': 'Not found',
                            'metadata': md,
                            'mailbox': mailbox,
                            'search': search}
            else:
                yield {
                    'error': 'Not found',
                    'mailbox': mailbox,
                    'search': search}

    def configure(self, args):
        return args

    async def await_messages(self, *prototypes, timeout=10):
        sleeptime, deadline = 0, (time.time() + timeout)
        while time.time() < deadline:
            if not self.messages:
                sleeptime = min(sleeptime + 0.01, 0.1)
                await asyncio.sleep(sleeptime)
            while self.messages:
                msg = self.messages.pop(0)
                if msg.get('req_type') in prototypes:
                    return msg
        return {}

    async def run(self):
        raise Nonsense('Unimplemented')

    async def web_run(self):
        try:
            return await self.run()
        except APIException:
            logging.exception('APIException %s' % self.NAME)
            raise
        except PermissionError:
            logging.info('Access denied in %s' % self.NAME)
        except:
            logging.exception('Failed to run %s' % self.NAME)
        finally:
            try:
                self.write_reply('', eof=True)
            except:
                pass
            for tf in self.tempfiles:
                tf.close()

    async def _run_wrapper(self):
        try:
            return await self.run()
        except:
            logging.exception('Exception in %s' % (self,))
            raise
        finally:
            for tf in self.tempfiles:
                tf.close()

    def sync_run(self):
        task = asyncio.ensure_future(self._run_wrapper())
        while not task.done():
            try:
                self.ev_loop.run_until_complete(task)
            except KeyboardInterrupt:
                if task:
                    task.cancel()
