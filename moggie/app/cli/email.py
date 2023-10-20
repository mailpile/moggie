# Relatively low level commands for generating or parsing/displaying e-mail.
#
# FIXMEs for generation:
#   - message templates
#   - accept a JSON data structure instead of arguments?
#      - Can this be done in a generic way in the CLICommand class
#   - accept a text repro too? Hmm.
#   - accept base64 encoded data as attachment args - needed for web API
#   - fancier date parsing
#   - do we want to generate strong passwords for users when ZIP encrypting?
#   - add some styling to outgoing HTML e-mails?
#   - search for attachments? define a URL-to-an-attachment?
#   - get send-via settings from config/context/...
#
#   - implement PGP/MIME encryption, autocrypt
#   - implement DKIM signatures
#
# FIXMEs for parsing:
#
#   - implement PGP/MIME encryption, autocrypt
#   - implement PGP/MIME signatures
#
import base64
import copy
import io
import logging
import os
import re
import sys
import time

from .command import Nonsense, CLICommand, AccessConfig
from .openpgp import CommandOpenPGP
from ...api.requests import *
from ...email.metadata import Metadata
from ...email.addresses import AddressInfo
from ...email.parsemime import MessagePart
from ...security.html import HTMLCleaner
from ...security.css import CSSCleaner
from ...util.dumbcode import from_json, dumb_decode


def _html_quote(t):
    return (t
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;'))


def _make_message_id(random_data=None):
    from binascii import hexlify
    return '<%s@mailpile>' % (
        str(hexlify(random_data or os.urandom(16)), 'utf-8'))


class CommandParse(CLICommand):
    """moggie parse [options] <terms>

    This command will load and parse e-mails matching the search terms,
    provided as files or standard input. Terms can also be full Moggie
    metadata objects (for API use).

    The output is either a human readable report (text or HTML), or a
    JSON data structure explaining the contents, structure and technical
    characteristics of the e-mail.

    ### Options

    %(OPTIONS)s

    ### Examples

        ...

    FIXME
    """
    NAME = 'parse'
    ROLES = AccessConfig.GRANT_READ
    WEBSOCKET = False
    WEB_EXPOSE = True
    CONNECT = False    # We manually connect if we need to!
    OPTIONS = [[
        (None, None, 'moggie'),
        ('--context=', ['default'], 'Context to use for default settings'),
        ('--format=',     ['text'], 'X=(text*|html|json|sexp)'),
        ('--stdin=',            [], None), # Allow lots to send stdin (internal)
        ('--input=',            [], 'Load e-mail from file X, "-" for stdin'),
        ('--username=',     [None], 'Username with which to access the email'),
        ('--password=',     [None], 'Password with which to access the email'),
        ('--or',           [False], 'Use OR instead of AND with search terms'),
        ('--allow-network',     [False], 'Allow outgoing network requests'),
        ('--forbid-filesystem', [False], 'Forbid loading local files'),
        ('--write-back',        [False], 'Write parse results to search index'),
        ] + CommandOpenPGP.OPTIONS_PGP_SETTINGS + [
    ],[
        (None, None, 'features'),
        ('--with-metadata=',    ['N'], 'X=(Y|N*), include moggie metadata'),
        ('--with-headers=',     ['Y'], 'X=(Y*|N), include parsed message headers'),
        ('--with-path-info=',   ['Y'], 'X=(Y*|N), include path through network'),
        ('--with-structure=',   ['Y'], 'X=(Y*|N), include message structure'),
        ('--with-text=',        ['Y'], 'X=(Y*|N), include message text parts'),
        ('--with-html=',        ['N'], 'X=(Y|N*), include raw message HTML'),
        ('--with-html-clean=',  ['N'], 'X=(Y|N*), include sanitized message HTML'),
        ('--with-html-text=',   ['Y'], 'X=(Y*|N), include text extracted from HTML'),
        ('--with-data=',        ['N'], 'X=(Y|N*), include attachment data'),
        ('--with-headprints=',  ['N'], 'X=(Y|N*), include header fingerprints'),
        ('--with-keywords=',    ['N'], 'X=(Y|N*), include search-engine keywords'),
        ('--with-autotags=',    ['N'], 'X=(Y|N*), include auto-tagging analysis'),
        ('--with-openpgp=',     ['N'], 'X=(Y|N*), process OpenPGP content'),
        ('--scan-moggie-zips=', ['Y'], 'X=(Y*|N), scan moggie-specific archives'),
        ('--scan-archives=',    ['N'], 'X=(Y|N*), scan all archives for attachments'),
        ('--zip-password=',        [], 'Password to use for ZIP decryption'),
        ] + CommandOpenPGP.OPTIONS_DECRYPTING
          + CommandOpenPGP.OPTIONS_VERIFYING + [
    #   ('--decrypt=',          ['N'], '? X=(Y|N*), attempt to decrypt contents'),
    #   ('--verify-pgp=',       ['N'], '? X=(Y|N*), attempt to verify PGP signatures'),
        ('--verify-dates=',     ['Y'], 'X=(Y*|N), attempt to validate message dates'),
        ('--verify-dkim=',      ['Y'], 'X=(Y*|N), attempt to verify DKIM signatures'),
        ('--dkim-max-age=',     [180], 'X=Max age (days, default=180) for DKIM validation'),
        ('--with-nothing=',     ['N'], 'X=(Y|N*), parse nothing by default'),
        ('--with-everything=',  ['N'], 'X=(Y|N*), try to parse everything!'),
        ('--ignore-index=',     ['N'], 'X=(Y|N*), Ignore contents of search index'),
    ]]

    class Settings:
        def __init__(self, **kwargs):
            def _opt(k, a, defaults):
                val = kwargs.get(a) or kwargs.get(k)
                if val is None:
                    if defaults:
                        val = defaults[-1]
                elif isinstance(val, list):
                    if len(val) > (1 if defaults else 0):
                        val = val[-1]
                    elif defaults:
                        val = defaults[-1]
                    else:
                        val = False
                if val in (True, 'Y', 'y', 'true', 'True', 'TRUE'):
                    return True
                if val in (False, 'N', 'n', 'false', 'False', 'FALSE', None):
                    return False
                return val

            w_none = _opt('--with-nothing=', 'with_nothing', None)
            w_all = _opt('--with-everything=', 'with_everything', None)
            for key, defaults, comment in CommandParse.OPTIONS[1]:
                if w_none:
                    defaults = [False]
                elif w_all and key not in (
                        '--ignore-index=',
                        '--dkim-max-age=',
                        '--verify-from=', '--decrypt-with=',
                        '--with-nothing=', '--with-everything='):
                    defaults = [True]
                if key:
                    attr = key[2:-1].replace('-', '_')
                    if False and w_all and key not in (
                            '--ignore-index=',
                            '--dkim-max-age=',
                            '--verify-from=', '--decrypt-with=',
                            '--with-nothing=', '--with-everything='):
                        self.__setattr__(attr, True)
                    self.__setattr__(attr, _opt(key, attr, defaults))

    class OpenPGPSettings:
        def __init__(self, cli_obj, settings, parsed_message):
            self.cli_obj = cli_obj
            self.context = cli_obj.context
            self.access = cli_obj.access
            self.worker = None

            if settings.decrypt_with and settings.verify_from:
                self.options = cli_obj.options

            else:
                self.options = copy.copy(cli_obj.options)
                if not settings.decrypt_with:
                    dw = self.options['--decrypt-with='] = []
                    for r in parsed_message.get('to', []):
                        dw.append('PGP:@PKEY:%s' % r['address'])
                    for r in parsed_message.get('cc', []):
                        dw.append('PGP:@PKEY:%s' % r['address'])

                if not settings.verify_from:
                    for hdr in ('from', 'reply-to'):
                        frm = parsed_message.get(hdr)
                        if frm and frm['address']:
                            self.options['--verify-from='].append(
                                'PGP:@CERT:%s' % frm['address'])

        def connect(self):
            self.worker = self.cli_obj.connect()
            return self.worker

    @classmethod
    def summarize_crypto(cls,
            part=None,
            errors=None,
            openpgp_decrypted_part=None,
            openpgp_verifications=None):
        state = (part and part.get('_CRYPTO')) or {}

        if errors:
            state['errors'] = state.get('errors', []) + errors
        if openpgp_decrypted_part is not None:
            state['openpgp_decrypted_part'] = openpgp_decrypted_part
        if openpgp_verifications:
            state['openpgp_verifications'] = [
                CommandOpenPGP.verification_as_dict(v)
                for v in openpgp_verifications]

        state['summary'] = '+'.join(s for s in [
            ('decrypted:%s' % state['openpgp_decrypted_part'])
                if 'openpgp_decrypted_part' in state else '',
            'verified' if state.get('openpgp_verifications') else '',
            'errors' if state.get('errors') else ''] if s)

        if state['summary'] and part:
            part['_CRYPTO'] = state
        return state

    @classmethod
    async def parse_openpgp(cls, cli_obj, settings, parsed_message):
        import sop
        # parse_message.decrypt is sync, but crypto ops are async, ugh.
        # So we do this in two passes, first we gather the things to
        # process, then we inject the results back into the parse tree.

        openpgp_cfg = cls.OpenPGPSettings(cli_obj, settings, parsed_message)
        _gathered = {}
        _done = {}

        def _decrypt_mep(part_bin, p_idx, part, parent):
            ctype = part['content-type']
            if p_idx in _done:
                data, verifications, sessionkeys = _done[p_idx]
                new_part = {'content-type': ['multipart/mixed', {}]}
                cls.summarize_crypto(new_part,
                    openpgp_decrypted_part=p_idx,
                    openpgp_verifications=verifications)
                del _done[p_idx]
                return (p_idx, p_idx+3), [(new_part, data)]

            if ctype[1].get('protocol') != 'application/pgp-encrypted':
                return None

            mpart = parent['_PARTS'][p_idx + 1]
            ppart = parent['_PARTS'][p_idx + 2]

            mpart_content = str(parent._bytes(mpart), 'latin-1').strip()
            if (mpart_content.lower().split() != ['version:', '1']
                    or ppart['content-type'][0] != 'application/octet-stream'):
                return None

            _gathered[p_idx] = ('decrypt', (parent._bytes(ppart),))
            return None

        def _verify_signed(part_bin, p_idx, part, parent):
            sig = None
            subs = []
            for p in parent['_PARTS'][p_idx+1:]:
                if p['_DEPTH'] == part['_DEPTH']:
                    break
                if (p['content-type'][0] == 'application/pgp-signature'
                        and p['_DEPTH'] == part['_DEPTH'] + 1):
                    sig = parent._bytes(p)
                    break
                else:
                    subs.append(p)

            if p_idx in _done:
                text, verifications = _done[p_idx]
                _crypto = cls.summarize_crypto(
                    openpgp_verifications=verifications)
                for p in subs:
                    p['_CRYPTO'] = _crypto
                del _done[p_idx]
                return None

            if sig:
                dpart = CommandOpenPGP.normalize_text(
                    parent._raw(parent['_PARTS'][p_idx + 1], header=True)
                    ).rstrip() + b'\r\n'
                _gathered[p_idx] = ('verify', (dpart, sig))
            return None

        def _decrypt_or_verify_inline(part_bin, p_idx, part, parent):
            text = part.get('_TEXT', '')
            decrypt = re.match('^\\s*-----BEGIN PGP MESSAGE', text, flags=re.S)
            verify = re.match('^\\s*-----BEGIN PGP SIGNED', text, flags=re.S)

            if p_idx in _done:
                ctype = part['content-type']
                cdisp = part.get('content-disposition')
                if decrypt:
                    data, verifications, sessionkeys = _done[p_idx]
                    new_part = {
                        'content-type': ['text/plain', {'charset': 'utf-8'}]}
                    cls.summarize_crypto(new_part,
                        openpgp_decrypted_part=p_idx,
                        openpgp_verifications=verifications)
                    if cdisp:
                        new_part['content-disposition'] = cdisp
                    del _done[p_idx]
                    return (p_idx, p_idx+1), [(new_part, data)]

                if verify:
                    text, verifications = _done[p_idx]
                    new_part = {
                        'content-type': ['text/plain', {'charset': 'utf-8'}]}
                    cls.summarize_crypto(new_part,
                        openpgp_verifications=verifications)
                    if cdisp:
                        new_part['content-disposition'] = cdisp
                    del _done[p_idx]
                    return (p_idx, p_idx+1), [(new_part, text)]

                del _done[p_idx]
                return None

            if decrypt:
                _gathered[p_idx] = ('decrypt', (text,))
            elif verify:
                _gathered[p_idx] = ('verify', (text,))
            return None

        errors = 0
        parsed_message['_OPENPGP_ERRORS'] = {}
        for tries in range(0, 3):
            parsed_message.decrypt({
                'multipart/encrypted': [_decrypt_mep],
                'multipart/signed': [_verify_signed],
                'text/plain:first': [_decrypt_or_verify_inline]})
            if not _gathered:
                break

            for idx, (op, data) in list(_gathered.items()):
                action = None
                try:
                    if op == 'decrypt':
                        action = CommandOpenPGP.get_decryptor(
                            openpgp_cfg, data=data[0])
                        _done[idx] = await action(*data)
                    elif op == 'verify':
                        action = CommandOpenPGP.get_verifier(
                            openpgp_cfg, sig=data[-1])
                        _done[idx] = await action(*data)
                except Exception as e:
                    if not action:
                        emsg = 'Missing information (keys?), cannot %s' % op
                    else:
                        logging.exception('%s failed' % op)
                        emsg = str(e)
                    cls.summarize_crypto(
                        parsed_message['_PARTS'][idx], errors=[emsg])
                    parsed_message['_OPENPGP_ERRORS'][idx] = True
                    errors += 1

                del _gathered[idx]
            if errors:
                break

    @classmethod
    async def Parse(cls, cli_obj, data,
            settings=None, allow_network=False,
            **kwargs):
        t0 = time.time()

        if settings is None:
            settings = cls.Settings(**kwargs)

        result = {}
        if isinstance(data, dict):
            result = data
        else:
            result['data'] = data
        data = result.get('data')

        md = result.get('metadata')
        html_magic = (settings.with_html
            or settings.with_html_text or settings.with_html_clean)

        quick_parse = None
        if data:
            from moggie.email.util import make_ts_and_Metadata, quick_msgparse
            from moggie.email.parsemime import parse_message

            quick_parse = quick_msgparse(data, 0)

        if not quick_parse:
            logging.warning('Failed to quick-parse: %s' % data)

        else:
            header_end, header_summary = quick_parse

            p = parse_message(data, fix_mbox_from=(data[:5] == b'From '))

            p['_HEADER_BYTES'] = header_end
            result['parsed'] = p

            if (md is None) or settings.ignore_index:
                ignored_ts, md = make_ts_and_Metadata(
                    time.time(), 0, header_summary, [], header_summary)
                result['metadata'] = md

            if settings.with_path_info:
                from moggie.security.headers import validate_smtp_hops
                p['_PATH_INFO'] = await validate_smtp_hops(p,
                    check_dns=allow_network)
            if settings.with_headers:
                p['_RAW_HEADERS'] = str(
                    data[:header_end], 'utf-8', 'replace').rstrip()
            if settings.with_openpgp and cli_obj:
                p.with_structure().with_text()
                await cls.parse_openpgp(cli_obj, settings, p)
            elif settings.with_structure:
                p.with_structure()

            if settings.verify_dates:
                # This happens *after* OpenPGP processing, so we can include
                # any signature dates in this check.
                from moggie.security.headers import validate_dates
                p['_DATE_VALIDITY'] = validate_dates(md.timestamp, p)

            if settings.scan_moggie_zips or settings.scan_archives:
                p.with_archive_contents(
                    moggie_archives=settings.scan_moggie_zips,
                    zip_archives=settings.scan_archives,
                    zip_passwords=settings.zip_password)

            need_keywords = settings.with_keywords or settings.with_autotags
            if settings.with_text or html_magic or need_keywords:
                p.with_text()
            if settings.with_data:
                p.with_data()

            if html_magic:
                for part in p.iter_parts(p):
                    if part.get('content-type', [None])[0] == 'text/html':
                        html = part['_TEXT']
                        if settings.with_html_clean:
                            from moggie.security.html import clean_email_html
                            part['_HTML_CLEAN'] = clean_email_html(md, p, part,
                                # FIXME: Make these configurable
                                inline_images=True,
                                remote_images=True,
                                target_blank=True)

                        if settings.with_html_text:
                            from moggie.security.html import html_to_markdown
                            part['_HTML_TEXT'] = html_to_markdown(html)

            if settings.with_headprints:
                from moggie.search.headerprint import HeaderPrints
                p['_HEADPRINTS'] = HeaderPrints(p)

            if settings.verify_dkim:
                from moggie.security.dkim import verify_all_async
                hcount = len(p.get('dkim-signature', []))
                verifications = []
                if not settings.ignore_index:
                    ts, verifications = md.get_dkim_status()
                if not verifications and p.get('dkim-signature'):
                    now = time.time()
                    maxage = 24 * 3600 * int(settings.dkim_max_age)
                    if (md.timestamp >= now - maxage) and allow_network:
                        verifications = await verify_all_async(
                            hcount, data, logger=logging)
                        if verifications:
                            md.set_dkim_status(verifications, ts=now)
                    else:
                        for dkim in p['dkim-signature']:
                            dkim['_DKIM_TOO_OLD'] = True
                            dkim['_DKIM_VERIFIED'] = False
                            dkim['_DKIM_PARTIAL'] = False
                for i, ok in enumerate(verifications):
                    dkim = p['dkim-signature'][i]
                    dkim['_DKIM_VERIFIED'] = ok
                    dkim['_DKIM_PARTIAL'] = False
                    if dkim.get('l'):
                        if int(dkim['l']) < (len(data) - header_end):
                            dkim['_DKIM_PARTIAL'] = True

            # Important: This must come last, it checks for the output of
            #            the above sections!
            if need_keywords:
                from moggie.search.extractor import KeywordExtractor
                kwe = KeywordExtractor()
                more, kws = kwe.extract_email_keywords(None, p)
                p['_KEYWORDS'] = sorted(list(kws))

            if settings.with_autotags and cli_obj:
                res = await cli_obj.worker.async_api_request(cli_obj.access,
                    RequestAutotagClassify(
                        context=cli_obj.get_context(),
                        keywords=p['_KEYWORDS']))
                p['_AUTOTAGS'] = res

            # Cleanup phase; depending on our --with-... arguments, we may
            # want to remove some stuff from the output.

            removing = []
            if not settings.with_headers:
                removing.extend(k for k in p if not k[:1] == '_')
                if settings.verify_dkim and 'dkim-signature' in removing:
                    removing.remove('dkim-signature')
                removing.append('_DATE_TS')
                removing.append('_RAW_HEADERS')
                removing.append('_mbox_separator')
                removing.append('_ORDER')
            if not settings.with_structure:
                removing.append('_HEADER_BYTES')
            if not (settings.with_structure
                    or settings.with_text
                    or settings.with_data
                    or html_magic):
                removing.append('_PARTS')
            for hdr in removing:
                if hdr in p:
                    del p[hdr]

            if settings.with_metadata:
                result['metadata'] = result['metadata'].parsed()
            else:
                del result['metadata']

            if not settings.with_structure and '_PARTS' in p:
                parts = p['_PARTS']
                for i in reversed(range(0, len(parts))):
                    ctype = parts[i]['content-type'][0]
                    if ctype.startswith('multipart/'):
                        parts.pop(i)
                    elif not (settings.with_data
                            or ctype in ('text/plain', 'text/html')):
                        parts.pop(i)
                    else:
                        for key in [k for k in parts[i] if k[:1] == '_']:
                            if key == '_TEXT' and settings.with_text:
                                pass
                            elif key.startswith('_HTML') and html_magic:
                                pass
                            elif key == '_DATA' and settings.with_data:
                                pass
                            else:
                                del parts[i][key]

        if 'data' in result:
            del result['data']

        result['_PARSE_TIME_MS'] = int(1000 * (time.time() - t0))
        return result

    def __init__(self, *args, **kwargs):
        self.emitted = 0
        self.settings = None
        self.searches = []
        self.messages = []
        super().__init__(*args, **kwargs)

    def configure(self, args):
        args = self.strip_options(args)

        # FIXME: Think about this, how DO we want to restrict access to the
        #        filesytem? It is probaby per user/context.
        self.allow_fs = not self.options['--forbid-filesystem'][-1]
        self.allow_network = self.options['--allow-network'][-1]
        self.write_back = self.options['--write-back'][-1]

        self.settings = self.Settings(**self.options)
        self.settings.zip_password = self.options['--zip-password=']

        if not self.allow_network:
            self.settings.verify_dkim = False
            if len(self.options['--verify-dkim=']) > 1:
                raise Nonsense('Cannot verify DKIM without network access')

        def _load(t, target):
            if t[:1] == '-':
                if self.options['--stdin=']:
                    # FIXME: This logic should be shared with other methods.
                    #        Bump up to the CLICommand class?
                    data = self.options['--stdin='].pop(0)
                    if data[:7] == 'base64:':
                        data = base64.b64decode(data[7:])
                else:
                    data = sys.stdin.buffer.read()
                target.append({
                    'stdin': True,
                    'data': data})
                return True
            elif t[:7] == 'base64:':
                # FIXME: This logic should be shared with other methods.
                #        Bump up to the CLICommand class?
                target.append({
                    'base64': True,
                    'data': base64.b64decode(t[7:])})
                return True
            elif self.allow_fs and (os.path.sep in t) and os.path.exists(t):
                with open(t, 'rb') as fd:
                    target.append({
                        'path': t,
                        'data': fd.read()})
                return True
            return False

        def _read_file(current, i, t, target):
            if _load(t, target):
                current[i] = None

        # This lets the caller provide messages for forwarding or replying to
        # directly, instead of searching. Anything left in the options after
        # this will be treated as a search term.
        for target, key in (
                (self.messages, '--input='),
                (self.messages, None)):
            current = self.options.get(key, []) if key else args
            i = None
            for i, t in enumerate(current):
                _read_file(current, i, t, target)
            if key:
                self.options[key] = [t for t in current if t]
            else:
                if i is None:
                    _read_file(['-'], 0, '-', self.messages)
                else:
                    args = [t for t in current if t]

        return self.configure2(args)

    def configure2(self, args):
        def _mailbox_and_terms(t):
            mailboxes, t = self.remove_mailbox_terms(t)
            return mailboxes, self.combine_terms(t)
        if args:
            self.searches.append(_mailbox_and_terms(args))
        self.searches.extend(
            _mailbox_and_terms(i) for i in self.options['--input='])
        self.options['--input='] = []
        return []

    def generate_markdown(self, parsed):
        def _indent(blob, indent='    ', code=False):
            if code:
                lines = ['```'] + blob.splitlines() + ['```']
            else:
                lines = blob.splitlines()
            return '\n'.join(indent+l for l in lines)

        def _wrap(words, maxlen=72, indent=''):
            lines = [indent]
            if isinstance(words, str):
                words = words.split()
            for word in words:
                if len(lines[-1]) + len(word) < maxlen:
                    lines[-1] += word + ' '
                else:
                    lines[-1] = lines[-1].rstrip()
                    lines.append(indent + word + ' ')
            return '\n'.join(lines)

        report = []
        if parsed.get('stdin'):
            report.append('# Parsed e-mail from standard input')
        elif parsed.get('search'):
            s = '%s' % parsed['search']
            if s[:1] == '{' and s[-1:] == '}':
                s = '(internal metadata)'
                report.append('# Parsed e-mail')
            else:
                if len(s) > 42:
                    s = s[:40] + '..'
                report.append('# Parsed e-mail from %s: %s'
                    % (parsed.get('mailbox') or 'search', s))

        if self.settings.with_metadata:
            md = parsed.get('metadata')
            if not md:
                report.append("""## Metadata unavailable""")
            else:
                if md['idx']:
                    msg = 'This is the metadata stored in the search index'
                else:
                    msg = 'This is the message metadata'
                msg_headers =  '\n'.join(
                    '    %s' % h for h in md['raw_headers'].splitlines())
                msg_pointers = '    \n'.join(
                    'pointer=%s,%s' % (p.ptr_type, dumb_decode(p.ptr_path),)
                    for p in md['ptrs'])
                report.append("""## Metadata\n\n%s:\n\n%s\n
    data_type=%s
    uuid=%s
    ts=%d, id=%s, parent=%s, thread=%s
    %s
    extras=%s""" % (msg,
                    msg_headers,
                    md['data_type'],
                    md['uuid'],
                    md['ts'],
                    md['idx'],
                    md.get('parent_id', '(none)'),
                    md.get('thread_id', '(none)'),
                    msg_pointers,
                    dict((k, md[k]) for k in md['_MORE'])))

        if 'error' in parsed:
            report.append('Error loading message: %s' % parsed['error'])

        elif 'parsed' in parsed:
            parsed = parsed['parsed']

            if self.settings.with_structure:
                # FIXME: We need to explain the structure of encrypted/signed
                #        messages! What became what? What was signed?
                atts = 0
                hlen = parsed['_HEADER_BYTES']
                structure = [
                   '## Message structure\n',
                   '   * Message header (%s bytes)' % hlen]
                replaced = {}
                for i, p in enumerate(parsed['_PARTS']):
                    p_crypto = p.get('_CRYPTO', {})
                    p_ctype = p['content-type']
                    p_disp = p.get('content-disposition') or [None, {}]

                    _id = '%d' % (i+1)
                    if '_REPLACE' in p:
                        replacement = '%d' % (p['_REPLACE']+1)
                        replaced[replacement] = _id

                    plen = p['_BYTES'][2] - p['_BYTES'][1]
                    filename = (
                        p_ctype[1].get('name') or
                        p_disp[1].get('filename'))
                    details = ', %s' % filename if filename else ''

                    if (p_crypto.get('openpgp_decrypted_part') is not None
                            and p_ctype[1].get('hp-legacy-display')):
                        details += ', header display'

                    verifs = p_crypto.get('openpgp_verifications')
                    if _id in replaced:
                        if verifs:
                            fmt = 'Verified Part %s (Part %s)'
                        else:
                            fmt = 'Decrypted Part %s (Part %s)'
                        _id = fmt % (replaced[_id], _id)
                    elif verifs:
                        _id = 'Verified Part ' + _id
                    elif p_crypto.get('openpgp_decrypted_part') is not None:
                        _id = 'Decrypted Part ' + _id
                    else:
                        _id = 'Part ' + _id
                    if verifs:
                        details += ', signed'
                    if p_crypto.get('errors'):
                        details += ', crypto failed'

                    basics = '   %s* %s: %s' % (
                        '   ' * p['_DEPTH'], _id, p_ctype[0])
                    details = ' (%d bytes%s)' % (plen, details)
                    if len(basics) + len(details) > 80:
                        details = '\n    %s%s' % (
                            '   ' * p['_DEPTH'], details)

                    structure.append(basics + details)
                    if filename:
                        atts += 1
                if atts:
                    structure.append("""
Note: Attachment sizes include the MIME encoding overhead, once decoded
      the files will be about 25% smaller.""")
                if replaced:
                    structure.append("""
Note: The list above includes synthetic parts which were generated by the
      decryption and/or signature verification processes; both "before"
      and "after" versions of the data.""")
                report.append('\n'.join(structure))

            if self.settings.verify_dates:
                from email.utils import formatdate
                dv = parsed['_DATE_VALIDITY']
                d0 = dv['timestamps'][0]
                dn = dv['timestamps'][-1]
                hints = ''
                if dv.get('time_went_backwards'):
                    hints = """\n
The header dates are not in the expected order, or appear to be from the
future. One or more machines involved in creating or delivering the message
probably has an incorrect clock."""
                elif dv.get('delta_large'):
                    hints = """\n
Large deltas may indicate that an old message has been resent, or that
one or more machines involved in creating or delivering the message have
incorrect clocks."""
                elif dv.get('delta_tzbug'):
                    hints = """\n
Deltas that are a multiple of 3600 seconds (1 hour) may be indicative of
incorrect time zones on one or more machines involved in processing the
message."""

                report.append("""\
## Dates and times

Dates and times in message span %d seconds from %d time zones.

   * Earliest: %s
   * Latest: %s%s\
""" % (         dv['delta'], len(dv['timezones']),
                formatdate(d0), formatdate(dn), hints))

            if self.settings.with_openpgp:
                from moggie.util.friendly import friendly_datetime
                report.append('## OpenPGP details')

                crypto_states = []
                for i, p in enumerate(parsed['_PARTS']):
                    p_crypto = p.get('_CRYPTO', {})
                    if p_crypto:
                        crypto_states.append((i, p_crypto))

                # FIXME: If we found protected headers, explain here

                r, bullet = [], '      * '
                if crypto_states or parsed.get('_OPENPGP_ERRORS'):
                    r0, last_summary = None, ''
                    for idx, state in crypto_states:
                        if state.get('errors'):
                            what = ', '.join(state['errors'])
                        else:
                            what = []
                            if 'openpgp_decrypted_part' in state:
                                what.append(
                                    'Decrypted OpenPGP Message (Part %d)'
                                    % (state['openpgp_decrypted_part']+1))
                            for v in state.get('openpgp_verifications', []):
                                what.append(
                                    'Signed by OpenPGP key 0x%s at %s'
                                    % (v['primary_fpr'][-16:],
                                       friendly_datetime(v['when'])))
                            what = ('\n'+bullet).join(sorted(list(set(what))))
                        if what != last_summary:
                            r0 = len(r)
                            r.append('   * Part %d:' % (idx+1))
                            r.append('%s%s' % (bullet, what))
                            last_summary = what
                        elif r0 is not None:
                            r[r0] = r[r0][:-1] + ', Part %d:' % (idx+1)
                    report.append('\n'.join(r))
                else:
                    report.append('No OpenPGP message or signature found.')

            if self.settings.with_headprints:
                report.append('## Header fingerprints\n\n'
                    + '\n'.join('   * %s: %s' % (hp, val)
                        for hp, val
                        in sorted(list(parsed['_HEADPRINTS'].items()))))

            if self.settings.with_path_info:
                hops = parsed.get('_PATH_INFO', {}).get('hops')
                if not hops:
                    status = 'No network path information found.\n'
                else:
                    status = 'Hops:\n\n'
                    for hop in reversed(hops):
                        status += '   * %s at %s\n' % (
                            hop.get('from_ip', '(unknown)'),
                            formatdate(hop['received_ts']))

                # FIXME: Any anomalies? Report DNS info?

                report.append('## Network path information\n\n%s'
                    % status.rstrip())

            if self.settings.verify_dkim:
                if not parsed.get('dkim-signature'):
                    status = 'No DKIM signatures found in header.\n'
                else:
                    status = ''
                    for dkim in parsed['dkim-signature']:
                        who = dkim.get('i', '')
                        if not who.endswith(dkim['d']):
                            who += ('/' if who else '') + dkim['d']

                        if dkim.get('_DKIM_TOO_OLD'):
                            summary = 'Too old'
                        elif dkim['_DKIM_VERIFIED']:
                            summary = 'Good'
                        else:
                            summary = 'Invalid'

                        status += '   * %s: v%s signature from %s%s\n' % (
                            summary, dkim['v'], who,
                            ' (partial body)' if dkim['_DKIM_PARTIAL'] else '')

                        status += '     (using %s %s with %s at %s)\n' % (
                            dkim['c'], dkim['a'], dkim['s'],
                            dkim.get('t', 'unspecified time'))

                report.append('## DKIM verification\n\n%s' % status.rstrip())

            if self.settings.with_keywords:
                kw = parsed.get('_KEYWORDS')
                if not kw:
                    report.append('## Keywords unavailable')
                else:
                    special = [k for k in kw if ':' in k]
                    others = [k for k in kw if ':' not in k]
                    report.append("""## Keywords

These are the %d searchable keywords for this message:

%s\n\n%s""" % (
                        len(special) + len(others),
                        _wrap(', '.join(special)),
                        _wrap(', '.join(others))))

            if self.settings.with_headers:
                from moggie.email.headers import ADDRESS_HEADERS, SINGLETONS

                seen = sorted([k for k in parsed if not k[:1] == '_'])
                report.append("## Message headers\n\n%s"
                    % _indent(parsed['_RAW_HEADERS'], code=True))

            if self.settings.with_text:
                report.append('## Message text')
                for part in parsed.iter_parts(parsed):
                    if part['content-type'][0] == 'text/plain':
                        report[-1] += ('\n\n'
                            + _indent(part['_TEXT'].strip(), code=True))

            if self.settings.with_html_text:
                report.append('## Message text (from HTML)')
                for part in parsed.iter_parts(parsed):
                    if part['content-type'][0] == 'text/html':
                        report[-1] += ('\n\n'
                            + _indent(part['_HTML_TEXT'].strip(), code=True))

            if self.settings.with_html:
                report.append('## Message HTML')
                for part in parsed.iter_parts(parsed):
                    if part['content-type'][0] == 'text/html':
                        report[-1] += ('\n\n'
                            + _indent(part['_TEXT'].strip(), code=True))

            if self.settings.with_html_clean:
                report.append('## Message HTML, sanitized')
                for part in parsed.iter_parts(parsed):
                    if part['content-type'][0] == 'text/html':
                        report[-1] += ('\n\n'
                            + _indent(part['_HTML_CLEAN'].strip(), code=True))

        return '\n\n'.join(report) + '\n\n'

    def emit_html(self, parsed):
        if parsed is not None:
            import markdown
            self.print(markdown.markdown(self.generate_markdown(parsed)))

    def emit_text(self, parsed):
        if parsed is not None:
            self.print(self.generate_markdown(parsed))

    def emit_sexp(self, parsed):
        if not self.emitted:
            self.print('(', nl='')
        if parsed is None:
            self.print(')')
        else:
            if self.emitted:
                self.print(',')
            self.print_sexp(parsed)
            self.emitted += 1

    def emit_json(self, parsed):
        if not self.emitted:
            self.print_json_list_start(nl='')
        if parsed is None:
            self.print_json_list_end()
        else:
            if self.emitted:
                self.print_json_list_comma()
            self.print_json(parsed)
            self.emitted += 1

    async def gather_emails(self):
        for mailboxes, search in self.searches:
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
                        terms=search)
                else:
                    request = RequestSearch(context=self.context, terms=search)
                result = await self.repeatable_async_api_request(
                    self.access, request)

            if result and 'emails' in result:
                for metadata in result['emails']:
                    req = RequestEmail(
                            metadata=metadata,
                            full_raw=True,
                            username=self.options['--username='][-1],
                            password=self.options['--password='][-1])
                    msg = await self.worker.async_api_request(self.access, req)
                    md = Metadata(*metadata)
                    if msg and 'email' in msg:
                        yield {
                            'data': base64.b64decode(msg['email']['_RAW']),
                            'metadata': md,
                            'mailbox': mailbox,
                            'search': search}
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

    def get_emitter(self):
        if self.options['--format='][-1] == 'sexp':
            return self.emit_sexp
        if self.options['--format='][-1] == 'html':
            return self.emit_html
        if self.options['--format='][-1] == 'text':
            return self.emit_text
        else:
            return self.emit_json

    async def run(self):
        emitter = self.get_emitter()

        if self.searches:
            async for message in self.gather_emails():
                emitter(await self.Parse(self, message, self.settings,
                    allow_network=self.allow_network))

        if self.messages:
            for message in self.messages:
                emitter(await self.Parse(self, message, self.settings,
                    allow_network=self.allow_network))

        emitter(None)


class CommandEmail(CLICommand):
    """moggie email [<options> ...]

    This command will generate (and optionally send) an e-mail, in
    accordance with the command-line options, Moggie settings, and relevant
    standards. It can be used as a stand-alone tool, but some features
    depend on having a live moggie backend.

    ## General options

    These options control the high-level behavior of this command; where
    it loads default settings from, what it does with the message once
    it has been generated, and how the output is formatted:

    %(moggie)s

    Note that by default, the e-mail is generated but not sent. If you
    want the e-mail to be sent you must specify `--send-at=` and/or
    `--send-via=`. Delayed sending requires the moggie backend be running
    at the requested time.

    The `--send-via=` command takes either server address, formatted as
    a URI (e.g. `smtp://user:pass@hostname:port/`), or the path to a
    local command to pipe the output too. Recognized protocols are `smtp`,
    `smtps` (SMTP over TLS) and `smtptls` (SMTP, requiring STARTTLS). The
    command specification can include Python `%%(...)s` formatting, for
    variables `from`, `to` (a single-argument comma-separated list) or
    `to_list` (one argument per recipient).

    ### Examples:

        # Generate an e-mail and dump to stdout, encapsulated in JSON:
        moggie email --format=json --subject="hello" --to=...

        # Use an alternate context for loading from, signature etc.
        moggie email --context='Work' --subject='meeting times' ...

        # Send a message via SMTP to root@localhost
        moggie email [...] \\
            --send-via=smtp://localhost:25 \\
            --send-to=root@localhost

        # Send a message using /usr/bin/sendmail
        moggie email [...] \\
            --send-via='/usr/bin/sendmail -f%%(from)s -- %%(to_list)s'


    ## Message headers

    Message headers can be specified on the command-line, using these
    options:

    %(headers)s

    If omitted, defaults are either loaded from the moggie configuration
    (for the active Context), derived from the headers of messages being
    forwarded or replied to or a best-effort combination of the two.

    ### Examples:

        # Set a subject and single recipient
        moggie email --subject='Hello friend!' --to=pal@exmaple.org ...

        # Set a custom header
        moggie email --header='X-Testing:Hello world' ...


    ## Message contents

    Moggie constructs e-mails with three main parts: a text part, an
    HTML part, and one or more attachments:

    %(content)s

    The most basic interface is to specify exactly the contents of
    each section using `--text=`, `--html=` and one or more `--attach=`
    options.

    Higher level usage is to use `--message=` and `--signature=` options to
    specify content, and let moggie take care of generating plain-text and
    HTML parts. This can be combined with replies, forwards or templates
    (see below) to generate quite complex messages according to common
    e-mail customs and best practices.

    ## Forwarding and replying

    The tool can automatically quote or forward other e-mails, reading
    them from local files (or stdin), or loading from the moggie search
    index and mail store. Options:

    %(searches)s

    The `--reply=` option is part of the high-level content generation;
    depending on the `--quoting=` option some or all of the text/HTML
    content of the replied-to messages will be included as quotes in the
    output message.

    When forwarding, the `inline` style (the `--forwarding=` option)
    is the default, where message text will be quoted in the message body,
    a default subject set and attachments will be re-attached to the new
    email. Specifying `--forwarding=attachment` will instead attach the
    the original mail unmodified as a `.eml` file with the `message/rfc822`
    MIME-type. Using `--forwarding=bounce` will output the original
    forwarded message entirely unmodified, aside from adding headers
    indicating it has been resent. Note that bounce forwarding (resending)
    is incompatible with most other `moggie email` features.

    **NOTE:** Be careful when using search terms with `--reply=` and
    `--forward=`, since searches matching multiple e-mails can result
    in very large output with unexpected contents. Sticking with
    `id:...` and/or tag-based searches is probably wise. If you are
    deliberately forwarding multiple messages, it may be a good idea to
    send them as a .ZIP archive (see encryption options below).

    ## Encryption, signatures, archives

    Moggie supports two forms of encryption, AES-encrypted ZIP archives
    and OpenPGP (PGP/MIME). Moggie also supports two types of digital
    signatures, DKIM and OpenPGP (PGP/MIME).

    %(encryption)s

    Note that default signing and encrypting preferences may be
    configured by the active moggie context.

    ZIP encryption is useful for sending confidential messages to people
    who do not have OpenPGP keys. All mainstream operating systems either
    include native support for AES-encrypted ZIP files, or have widely
    available free tools which do so. However, users should be made aware
    that encrypted ZIP files leak a significant amount of metadata about
    their contents, and communicating the password will have to be done
    using a side-channel (e.g. a secure message or phone call). These
    archives are only as secure as the passwords and the channels used to
    transmit them.

    ### Examples:

        # Encrypt to a couple of OpenPGP keys; note it is the caller's
        # job to ensure the e-mail recipients match. The keys must be
        # on the caller's GnuPG keychain.
        moggie email [...] \\
            --encrypt=all \\
            --encrypt-to=PGP:61A015763D28D410A87B197328191D9B3B4199B4 \\
            --encrypt-to=PGP:CB484157EC53EEE53C1369C3C5728DA522425313

        # Sign using both OpenPGP and DKIM identities
        moggie email [...] \\
            --sign-with=PGP:61A015763D28D410A87B197328191D9B3B4199B4 \\
            --sign-with=DKIM:/path/to/secret-key

        # Put the attachments in an encrypted ZIP file
        moggie email [...] \\
            --encrypt=attachments \\
            --zip-password="super-strong-secret-diceware-passphrase"

    As a convenience, a ZIP password of 'NONE' will generate an
    unencrypted ZIP archive, in case you just want moggie to generate
    a ZIP file for you:

        moggie email [...] \\
            --encrypt=attachments --zip-password=NONE

    ## Known bugs and limitations

    A bunch of the stuff above isn't yet implemented. It's a lot!
    This is a work in progress.

    """
    _NOTES = """

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
     - Add PGP and DKIM support. What about AGE? And S/MIME?

    """
    NAME = 'email'
    ROLES = AccessConfig.GRANT_READ
    WEBSOCKET = False
    WEB_EXPOSE = True
    CONNECT = False    # We manually connect if we need to!
    OPTIONS = [[
        (None, None, 'moggie'),
        ('--context=', ['default'], 'Context to use for default settings'),
        ('--format=',   ['rfc822'], 'X=(rfc822*|text|json|sexp)'),
        ('--send-to=',          [], 'Address(es) to send to (igores headers)'),
        ('--send-at=',          [], 'X=(NOW|+seconds|a Unix timestamp)'),
        ('--send-via=',         [], 'X=(smtp|smtps)://[user:pass@]host:port'),
        ('--stdin=',            [], None), # Allow lots to send stdin (internal)
    ],[
        (None, None, 'headers'),
        ('--from=',      [],  'name <e-mail> OR account ID.'),
        ('--bcc=',       [],  'Hidden recipient (BCC)'),
        ('--to=',        [],  'To: recipient'),
        ('--cc=',        [],  'Cc: recipient'),
        ('--date=',      [],  'Message date, default is "now"'),
        ('--subject=',   [],  'Message subject'),
        ('--header=',    [],  'X="header:value", set arbitrary headers'),
    ],[
        (None, None, 'content'),
        ('--text=',      [],  'X=(N|"actual text content")'),
        ('--html=',      [],  'X=(N|"actual HTML content")'),
        ('--message=',   [],  'A snippet of text to add to the message'),
#FIXME: ('--template=',  [],  'Use a file or string as a message template'),
        ('--signature=', [],  'A snippet of text to append to the message'),
        ('--8bit',       [],  'Emit unencoded 8-bit text and HTML parts'),
        ('--attach=',    [],  'mimetype:/path/to/file'),
    ],[
        (None, None, 'searches'),
        ('--reply=',          [], 'Search terms, path to file or - for stdin'),
        ('--forward=',        [], 'Search terms, path to file or - for stdin'),
        ('--reply-to=',  ['all'], 'X=(all*|sender)'),
        ('--forwarding=',     [], 'X=(inline*|attachment|bounce)'),
        ('--quoting=',        [], 'X=(html*|text|trim*|below), many allowed'),
        ('--username=',   [None], 'Username with which to access email'),
        ('--password=',   [None], 'Password with which to access email'),
    ],[
        (None, None, 'encryption'),
        ('--decrypt=',      [], 'X=(N|auto|false|true)'),
        ('--encrypt=',      [], 'X=(N|all|attachments)'),
        ('--zip-password=', [], 'Password to use for ZIP encryption')]
        + CommandOpenPGP.OPTIONS_SIGNING
        + CommandOpenPGP.OPTIONS_ENCRYPTING
        + CommandOpenPGP.OPTIONS_PGP_SETTINGS + [
        ('--pgp-headers=',    [], 'X=(N|auto|sign|subject|all)')]
        + CommandOpenPGP.OPTIONS_AUTOCRYPT]

    DEFAULT_QUOTING = ['html', 'trim']
    DEFAULT_FORWARDING = ['html', 'inline']

    def __init__(self, *args, **kwargs):
        self.replying_to = []
        self.forwarding = []
        self.attachments = []
        self.headers = {}
        super().__init__(*args, **kwargs)

    def _load_email(self, fd):
        from moggie.email.parsemime import parse_message
        if fd in (sys.stdin.buffer, sys.stdin) and self.options['--stdin=']:
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
                  (self.forwarding,  '--forward=')):
            current = self.options.get(key, [])
            for i, t in enumerate(current):
                as_file(key, i, t, target, self._load_email)
            self.options[key] = [t for t in current if t]

        # Similarly, gather attachment data, if it is local. Anything left
        # in the attachment option will be treated as a remote reference.
        key = '--attach='
        current = self.options.get(key, [])
        for i, t in enumerate(current):
            if ':' in t:
                mt, path = t.split(':', 1)
            else:
                mt, path = 'application/octet-stream', t
            as_file(key, i, path, self.attachments,
                lambda fd: (mt, os.path.basename(path), fd.read()))
        self.options[key] = [t for t in current if t]

        # Complain if the user attempts both --text= and --message= style
        # composition; we want one or the other!
        if self.options.get('--message=') and (
                self.options['--text='] not in ([], ['N']) or
                self.options['--html='] not in ([], ['N'])):
            raise Nonsense('Use --message= or --text=/--html= (not both)')

        # Complain if the user tries to both compose a message and bounce
        # at the same time - bounces have already been fully composed.
        if 'bounce' in self.options.get('--forwarding='):
            if (len(self.forwarding) > 1
                    or len(self.options.get('--forward=')) > 1):
                raise Nonsense('Please only bounce/resend one message at a time.')
            for opt, val in self.options.items():
                if val and opt not in (
                        '--context=', '--format=',
                        '--forwarding=', '--forward=',
                        '--reply-to=',
                        '--from=', '--send-to=', '--send-at=', '--send-via='):
                    raise Nonsense('Bounced messages cannot be modified (%s%s)'
                        % (opt, val))
            if not self.options.get('--send-to='):
                raise Nonsense('Please specify --send-to= when bouncing')

        # Parse any supplied dates...
        import datetime
        key = '--date='
        current = self.options.get(key, [])
        for i, dv in enumerate(current):
            try:
                current[i] = datetime.datetime.fromtimestamp(int(dv))
            except ValueError:
                raise Nonsense('Dates must be Unix timestamps (FIXME)')

        # Parse and expand convert e-mail address options
        from moggie.email.addresses import AddressHeaderParser
        for opt in ('--from=', '--to=', '--cc=', '--bcc=', '--send-to='):
            if self.options[opt]:
                new_opt = []
                for val in self.options[opt]:
                    new_opt.extend(AddressHeaderParser(val))
                for val in (new_opt or [None]):
                    if not val or '@' not in (val.address or ''):
                        raise Nonsense('Failed to parse %s' % opt)
                self.options[opt] = new_opt

        CommandOpenPGP.configure_keys(self)
        CommandOpenPGP.configure_passwords(self,
            which=('--pgp-password=', '--zip-password='))

        if not self.options['--pgp-headers=']:
            # FIXME: Check user preferences
            self.options['--pgp-headers='] = ['N']

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
        return args

    def configure2(self, args):
        if args:
            raise Nonsense('Unknown args: %s' % args)
        return args

    def text_part(self, text, mimetype='text/plain', ctattrs=[],
            no_enc=False, always_enc=False):
        try:
            if always_enc:
                no_enc = False
                raise ValueError('Encoding required')
            data = str(bytes(text, 'us-ascii'), 'us-ascii')
            enc = '7bit'
        except (ValueError, UnicodeEncodeError):
            if no_enc or self.options['--8bit']:
                enc = '8bit'
                data = text
            else:
                import email.base64mime as b64
                data = b64.body_encode(bytes(text, 'utf-8'))
                enc = 'base64'
        data = data.replace('\r', '').replace('\n', '\r\n')
        return ({
                'content-type': [mimetype, ('charset', 'utf-8')] + ctattrs,
                'content-disposition': 'inline',
                'content-transfer-encoding': enc
            }, data.strip() + '\r\n')

    def multi_part(self, mtype, parts, attrs=None):
        from moggie.email.headers import format_headers
        from moggie.util.mailpile import b64c, sha1b64
        import os
        boundary = b64c(sha1b64(os.urandom(32)))
        bounded = ['\r\n--%s\r\n%s%s' % (
                boundary,
                format_headers(headers),
                body.strip()
            ) for headers, body in parts]
        bounded.append('\r\n--%s--' % boundary)
        ctype = ['multipart/%s' % mtype, ('boundary', boundary)]
        if attrs:
            ctype.extend(attrs)
        return ({
                'content-type': ctype,
                'content-transfer-encoding': '7bit'
            }, '\r\n'.join(bounded).strip())

    def attach_part(self, mimetype, filename, data):
        import email.base64mime as b64
        ctyp = [mimetype]
        disp = ['attachment']
        if filename:
            disp.append(('filename', filename))
        data = b64.body_encode(data).strip()
        data = data.replace('\r', '').replace('\n', '\r\n')
        return ({
                'content-type': ctyp,
                'content-disposition': disp,
                'content-transfer-encoding': 'base64'
            }, data)

    def get_zip_password(self):
        # FIXME: Generate a password? How do we tell the user?
        #        Only do this if --zip-password=auto ?

        zip_pw = self.options.get('--zip-password=')
        if zip_pw:
            zip_pw = bytes(zip_pw[-1], 'utf-8')
            return zip_pw

        raise Nonsense('FIXME: need a password')

    def protect_headers(self, headers, body, obscure=False):
        # FIXME: Sometimes obscure=['subject', 'to', 'cc', 'from'] ??
        """
        Copy and/or obscure specific headers for, as per:
        https://datatracker.ietf.org/doc/draft-ietf-lamps-header-protection/
        """
        # Check user preferences
        prefs = self.options['--pgp-headers='][-1]
        if prefs == 'N':
            return headers, body
        if obscure:
            if prefs == 'sign':
                obscure = False
            elif prefs == 'subject':
                obscure = ['subject']
            elif prefs == 'all':
                obscure = [
                    h for h in self.headers
                    if h[:8] not in ('mime-ver', 'content-', 'date')]

        outer_headers = copy.copy(self.headers)
        obscured = []
        removed = []

        if isinstance(obscure, list):
            # FIXME: This needs work and research
            for h in obscure:
                obscured.append(h)
                if h == 'subject':
                    self.headers['subject'] = ['[...]']
                elif h == 'to':
                    self.headers['to'] = ['undisclosed-recipients:;']
                elif h == 'from':
                    self.headers['from'] = ['undisclosed-sender:;']
                elif h == 'message-id':
                    self.headers['message-id'] = _make_message_id()
                elif h in self.headers:
                    removed.append(h)
                    del self.headers[h]
        elif obscure:
            obscured.append('subject')
            self.headers['subject'] = ['...']

        # FIXME: That draft also suggests writing directly into the message
        #        text/plain or text/html parts, instead of adding a part.
        #        Do we want to move to that?
        if obscured:
            from moggie.email.headers import HEADER_ORDER, HEADER_CASEMAP
            from moggie.email.headers import format_header

            # This generates the legacy display part, which is for human
            # consumption - so we use the format_header() function but
            # stub out and disable most of the quoting that would normally
            # take place.
            display = []
            def null_quote(t, **kwargs):
                return t
            def null_norm(ah):
                try:
                    return '%s <%s>' % (ah.fn, ah.address)
                except AttributeError:
                    return ah.normalized()
            displaying = [oh for oh in outer_headers if oh in obscured]
            displaying.sort(key=lambda k: (HEADER_ORDER.get(k.lower(), 0), k))
            for h in displaying:
                display.append(format_header(h, outer_headers[h],
                    text_quote=null_quote,
                    normalizer=null_norm))

            display = '\n'.join(display) + '\n'
            display_html = (
                '<div class="header-protection-legacy-display"><pre>\n' +
                display.replace('<', '&lt;').replace('>', '&gt;') +
                '</pre></div>')

            # FIXME: We should be editing the text/plain and text/html
            #        parts here, not adding a new layer and new parts.
            #        This will take some refactoring.
            headers, body = self.multi_part('mixed', [
                self.text_part(display, 'text/plain',
                               ctattrs=[('hp-legacy-display', '1')]),
                (headers, body)])

        headers['content-type'].append(('protected-headers', 'v1'))
        for h in outer_headers:
            # FIXME: Is omitting Autocrypt the right thing here?
            if (h not in ('mime-version', 'autocrypt')
                   and not h.startswith('content-')):
                headers[h] = outer_headers[h]

        if removed:
            headers['HP-Removed'] = [', '.join(
                HEADER_CASEMAP.get(h, h) for h in removed)]
        if obscured:
            headers['HP-Obscured'] = []
            for hdr in (o for o in obscured if o not in removed):
                headers['HP-Obscured'].append(
                    format_header(hdr, self.headers[hdr]))

        return headers, body

    async def encrypt_to_recipients(self, headers, body):
        from moggie.email.headers import format_headers

        encryptor, how, ext, emt = CommandOpenPGP.get_encryptor(self)
        if not encryptor:
            raise Nonsense('Unable to encrypt')

        # FIXME: Check prefs? Skip this if user always wants PGP/MIME
        simplify = self.options['--pgp-headers='][-1] in ('N', 'auto')
        if simplify and (headers['content-type'][0] == 'text/plain'):
            # If the text was base64 encoded, undo that. PGP is armor.
            if headers.get('content-transfer-encoding', ['']) == 'base64':
                headers['content-transfer-encoding'] = ['7bit']
                body = str(base64.b64decode(body), 'utf-8')
            ciphertext = await encryptor(body)
            return await self.sign_message(
                headers, ciphertext, openpgp=False, dkim=True)

        headers, body = self.protect_headers(headers, body, obscure=True)

        # FIXME: If this is a draft or first-in-thread, add Autocrypt Gossip
        #        headers here?

        ciphertext = await encryptor(format_headers(headers) + body)
        fn = '%s-encrypted-message.%s' % (how, ext)
        parts = [
            ({'content-type': [emt]}, 'Version: 1'),
            ({
                'content-type': ['application/octet-stream', ('name', fn)],
                'content-disposition': ['inline', ('filename', fn)],
             }, ciphertext)]

        h, b = self.multi_part('encrypted', parts, attrs=[('protocol', emt)])
        return await self.sign_message(h, b, openpgp=False, dkim=True)

    async def sign_message(self, headers, body, openpgp=True, dkim=True):
        from moggie.email.headers import format_headers

        ids = CommandOpenPGP.get_signing_ids_and_keys(self)

        if openpgp and ids['PGP']:
            signer, how, ext, smt = CommandOpenPGP.get_signer(self,
                html=True, clear=False)
            fn = '%s-digital-signature.%s' % (how, ext)
            headers, body = self.protect_headers(headers, body)
            body = body.strip()
            signature, micalg = await signer(
                format_headers(headers) + body + '\r\n')
            parts = [
                (headers, body),
                ({
                    'content-type': [smt, ('name', fn)],
                    'content-disposition': ['attachment', ('filename', fn)],
                }, signature)]
            headers, body = self.multi_part('signed', parts,
                attrs=[('protocol', smt), ('micalg', micalg)])

        if dkim and ids['DKIM']:
            pass

        return (headers, body)

    async def clearsign(self, text):
        ids = CommandOpenPGP.get_signing_ids_and_keys(self)
        if not ids['PGP']:
            return text
        signer, _, _, _ = CommandOpenPGP.get_signer(self, clear=True)
        return (await signer(text))[0]

    async def attach_encrypted_attachments(self, text_parts=None):
        from moggie.storage.exporters.maildir import ZipWriter
        import io, base64

        mimetype = 'x-mailpile/zip'
        filename = 'message.zip' if text_parts else 'attachments.zip'
        encryptor, how, ext, emt = CommandOpenPGP.get_encryptor(self)

        passphrase = None
        if not encryptor or self.options.get('--zip-password'):
            passphrase = self.get_zip_password()
        if passphrase in (b'', b'NONE'):
            passphrase = None

        added = 0
        now = time.time()
        fd = io.BytesIO()
        zw = ZipWriter(fd, password=passphrase)
        if text_parts:
            for headers, text_data in text_parts:
                if headers['content-type'][0] == 'text/html':
                    fn = 'message-body.html'
                else:
                    fn = 'message-body.txt'
                zw.add_file(fn, now, bytes(text_data, 'utf-8'))
                added += 1
        for _unused, fn, data in self.attachments:
            zw.add_file(fn, now, data)
            added += 1
        zw.close()
        data = fd.getvalue()

        if not added:
            return None

        # If we are PGP or AGE encrypting the file, that transformation
        # happens here.
        if encryptor is not None:
            filename += '.%s' % ext
            data = bytes(await encryptor(data), 'utf-8')

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
        if isinstance(message, MessagePart):
            message.with_text()
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

        strategy = ','.join(self.options['--quoting='] or self.DEFAULT_QUOTING)
        quote_text = quote_html = ''

        def _quotebrackets(txt):
            return ''.join('> %s' % l for l in txt.strip().splitlines(True))
        quote_text = _quotebrackets(self.get_message_text(message))

        if 'html' in strategy or ('text' not in strategy and not quote_text):
            quote_html = self.get_message_text(message, mimetype='text/html')
            if quote_html:
                quote_html = '<blockquote>%s</blockquote>' % quote_html
                if 'text' not in strategy:
                    quote_text = None

        if quote_text and not quote_html:
            # Note: _quotebrackets becomes <blockquote>
            quote_html = self.text_to_html(quote_text)
        elif quote_html and not quote_text:
            quote_text = self.html_to_text(quote_html)

        if quote_text:
            if 'trim' in strategy and len(quote_text) > 1000:
                quote_text = (quote_text[:1000].rstrip()) + ' ...\n'
            quote_text = '%s wrote:\n%s' % (frm, quote_text)

        if quote_html:
            # FIXME: add our own CSS definitions, which the cleaner will then
            #        apply for prettification?
            quote_html = '<p>%s wrote:</p>\n%s' % (
                _html_quote(frm),
                HTMLCleaner(quote_html,
                    stop_after=(2000 if ('trim' in strategy) else None),
                    css_cleaner=CSSCleaner()).close())

        return strategy, quote_text, quote_html

    def collect_inline_forwards(self, message, count=0):
        strategy = ','.join(
            self.options['--forwarding='] or self.DEFAULT_FORWARDING)
        if 'inline' not in strategy:
            return strategy, '', ''

        fwd_text = self.get_message_text(message, mimetype='text/plain')
        fwd_html = ''
        if 'html' in strategy or ('text' not in strategy and not fwd_text):
            fwd_html = self.get_message_text(message, mimetype='text/html')
            if fwd_html and 'text' not in strategy:
                fwd_text = None
        if fwd_text and not fwd_html:
            fwd_html = self.text_to_html(fwd_text)
        elif fwd_html and not fwd_text:
            fwd_text = self.html_to_text(fwd_html)

        meta = []
        for hdr in ('Date', 'To', 'Cc', 'From', 'Subject'):
            vals = message.get(hdr.lower(), [])
            if not isinstance(vals, list):
                vals = [vals]
            if vals:
                if hdr in ('Date', 'Subject'):
                    meta.append((hdr, ' '.join(vals)))
                else:
                    def _fmt(ai):
                        if ai.get('fn'):
                            return '%(fn)s <%(address)s>' % ai
                        else:
                            return '<%(address)s>' % ai
                    meta.append((hdr, ', '.join(_fmt(v) for v in vals)))

        # FIXME: Is there a more elegant way to do this? Is this fine?
        fwd_text += '\n'
        fwd_html += '\n'
        for mt, filename, _ in self.forward_attachments(message, count=count):
            fwd_text += '\n[%s]' % filename
            fwd_html += '<p><tt>[%s]</tt></p>' % _html_quote(filename)

        fwd_text_meta = ("""\
-------- Original Message --------\n%s\n"""
            % ''.join('%s: %s\n' % (h, v) for h, v in meta))
        fwd_text = fwd_text_meta + (fwd_text or '(Empty message)\n')

        fwd_html_meta = ("""\
<p class="fwdMetainfo">-------- Original Message --------<br>\n%s</p>"""
                % ''.join('  <b>%s:</b> %s<br>\n' % (h, _html_quote(v))
                          for h, v in meta))
        fwd_html = '<div class="forwarded">\n%s\n</div>' % HTMLCleaner(
            fwd_html_meta + '\n\n' +
                (fwd_html or '<p><i>(Empty message)</i></p>'),
            css_cleaner=CSSCleaner()).close()

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

        for sig in self.options['--signature=']:
            t, h = self.text_and_html(sig)
            text.append('-- \r\n' + t)
            html.append('<br><br>--<br>\n' + self.wrap_text(h))

        for i, msg in enumerate(self.forwarding):
            strategy, f_txt, f_htm = self.collect_inline_forwards(msg, count=i)
            if f_txt and f_htm:
                text.append(f_txt)
                html.append(f_htm)

        if not want_text:
            text = []
        if not want_html:
            html = []
        return text, html

    def forward_attachments(self, msg, decode=base64.b64decode, count=0):
        strategy = ','.join(
            self.options['--forwarding='] or self.DEFAULT_FORWARDING)
        if decode in (None, False):
            decode = lambda d: None

        if 'inline' in strategy:
            for i, part in enumerate(msg['_PARTS']):
                mtyp, mattr = part.get('content-type', ['', {}])
                disp, dattr = part.get('content-disposition', ['', {}])
                if disp == 'attachment':
                    n = ('%d.%d-' % (count, i))
                    yield (
                        part['content-type'],
                        n + dattr.get('filename', mattr.get('name', 'att.bin')),
                        decode(part['_DATA']))

        elif 'attachment' in strategy:
            import datetime
            ts = datetime.datetime.fromtimestamp(msg.get('_DATE_TS', 0))
            subject = msg['from']['address']
            yield (
                'message/rfc822',
                '%4.4d%2.2d%2.2d-%2.2d%2.2d_%s_.eml' % (
                    ts.year, ts.month, ts.day, ts.hour, ts.minute, subject),
                decode(msg['_RAW']))

    async def render(self):
        from moggie.email.headers import HEADER_CASEMAP, format_headers

        for hdr_val in self.options['--header=']:
            hdr, val = hdr_val.split(':', 1)
            if hdr.lower() in HEADER_CASEMAP:
                hdr = hdr.lower()
            h = self.headers[hdr] = self.headers.get(hdr, [])
            h.append(val)

        for hdr, opt in (
                ('from',    '--from='),
                ('to',      '--to='),
                ('cc',      '--cc='),
                ('date',    '--date='),
                ('subject', '--subject=')):
            h = self.headers[hdr] = self.headers.get(hdr, [])
            for v in self.options.get(opt, []):
                # Someone should spank me for playing golf
                (h.extend if isinstance(v, list) else h.append)(v)
            if not h:
                del self.headers[hdr]

        if 'date' not in self.headers:
            import datetime
            self.headers['date'] = [datetime.datetime.now()]

        if 'mime-version' not in self.headers:
            self.headers['mime-version'] = 1.0
        if 'message-id' not in self.headers:
            self.headers['message-id'] = _make_message_id()

        # Sanity checks
        if len(self.headers.get('from', [])) != 1:
            raise Nonsense('There must be exactly one From address!')
        if len(self.headers.get('date', [])) > 1:
            raise Nonsense('There can only be one Date!')

        ac_header = await CommandOpenPGP.get_autocrypt_header(
            self, self.headers['from'][0].address)
        if ac_header is not None:
            self.headers[ac_header[0]] = ac_header[1]

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
            if not (want_html or want_text):
                want_html = True if not html_opt else False
                want_text = True if not text_opt else False
            text_opt, html_opt = self.generate_text_parts(want_text, want_html)

        # FIXME: Is this where we fork, on what the output format is?

        encryption = (self.options.get('--encrypt=') or ['N'])[-1].lower()
        raw_text = (encryption == 'all' and not self.options['--encrypt-to='])
        encode = (len(self.options['--sign-with=']) > 0) and not raw_text

        parts = []
        raw_text_parts = []
        for i, msg in enumerate(self.forwarding):
            self.attachments.extend(self.forward_attachments(msg, count=i))

        text_opt = [t for t in text_opt if t not in ('', 'Y')]
        if want_text and text_opt:
            raw_text_part = '\r\n\r\n'.join(t.strip() for t in text_opt)
            parts.append(self.text_part(raw_text_part, always_enc=encode))
            raw_text_parts.append((parts[-1][0], raw_text_part))

        html_opt = [t for t in html_opt if t not in ('', 'Y')]
        if want_html and html_opt:
            raw_text_part = '\r\n\r\n'.join(html_opt)
            parts.append(self.text_part(raw_text_part,
                mimetype='text/html',
                always_enc=encode))
            raw_text_parts.append((parts[-1][0], raw_text_part))

        clearsignable = ((not self.attachments)
            and (len(raw_text_parts) == len(parts) == 1)
            and (parts[0][0]['content-type'][0] == 'text/plain')
            and (self.options['--pgp-clearsign='][-1] in ('Y', 'y', '1')))

        if encryption == 'all' and not self.options['--encrypt-to=']:
            # Create an encrypted .ZIP with entire message content
            clearsignable = False
            _zp = await self.attach_encrypted_attachments(
                text_parts=raw_text_parts)
            if _zp:
                parts = [_zp]
        else:
            if len(parts) > 1:
                parts = [self.multi_part('alternative', parts)]

            if (encryption == 'attachments') and self.attachments:
                # This will create an encrypted .ZIP with our attachments only
                parts.append(await self.attach_encrypted_attachments())
            else:
                for mimetype, filename, data in self.attachments:
                    parts.append(self.attach_part(mimetype, filename, data))

        if len(parts) > 1:
            header, body = self.multi_part('mixed', parts)
            clearsignable = False
        elif parts:
            header, body = parts[0]
        else:
            header, body = {}, ''

        if encryption == 'all' and self.options['--encrypt-to=']:
            # Encrypt to someone: a PGP or AGE key - note this also
            # takes care of signing if necessary, as we prefer combined
            # encryption+signatures whenever possible.
            header, body = await self.encrypt_to_recipients(header, body)

        elif self.options['--sign-with=']:
            if clearsignable:
                header, body = parts[0] = self.text_part(
                    await self.clearsign(raw_text_parts[0][-1]))

            # Sign entire message
            header, body = await self.sign_message(header, body,
                openpgp=(not clearsignable))

        self.headers.update(header)
        return ''.join([format_headers(self.headers), body])

    def _reply_addresses(self):
        senders = {}
        recipients = {}
        def _add(_hash, _ai):
             if not isinstance(_ai, AddressInfo):
                 _ai = AddressInfo(**_ai)
             _hash[_ai['address']] = _ai
        for email in self.replying_to:
             _add(senders, email['from'])
             for ai in email.get('to', []) + email.get('cc', []):
                 _add(recipients, ai)
        return senders, recipients

    def gather_subject(self):
        def _re(s):
            w1 = (s.split()[0] if s else '').lower()
            return s if (w1 == 're:') else 'Re: %s' % s
        def _fwd(s):
            w1 = (s.split()[0] if s else '').lower()
            return s if (w1 == 'fwd:') else 'Fwd: %s' % s

        subjects = []
        for msg in self.replying_to:
            subj = msg.get('subject')
            if subj:
                 subjects.append(_re(subj))
        for msg in self.forwarding:
            subj = msg.get('subject')
            if subj:
                 subjects.append(_fwd(subj))

        if subjects:
            subject = subjects[0]
            if len(subjects) > 1:
                subject += ' (+%d more)' % (len(subjects) - 1)
            self.options['--subject='] = [subject]

    def gather_from(self, senders_and_recipients=None):
        senders, recipients = senders_and_recipients or self._reply_addresses()

        # Check the current context for addresses that were on the
        # recipient list. If none are found, use the main address for
        # the context. If we are replying to ourself, prefer that!
        ctx = self.cfg.contexts[self.context]
        ids = self.cfg.identities
        for _id in (ids[i] for i in ctx.identities):
            if _id.address in senders:
                self.options['--from='] = [_id.as_address_info()]
                return
        for _id in (ids[i] for i in ctx.identities):
            if _id.address in recipients:
                self.options['--from='] = [_id.as_address_info()]
                return

        # Default to our first identity (falls through if there are none)
        for _id in (ids[i] for i in ctx.identities):
            self.options['--from='] = [_id.as_address_info()]
            return

        raise Nonsense('No from address, aborting')

    def gather_to_cc(self, senders_and_recipients=None):
        senders, recipients = senders_and_recipients or self._reply_addresses()

        frm = self.options['--from='][0].address

        self.options['--to='].extend(
            a.normalized() for a in senders.values() if a.address != frm)
        if self.options['--reply-to='][-1] == 'all':
            self.options['--cc='].extend(
                a.normalized() for a in recipients.values()
                if a.address != frm and a.address not in senders)

    async def gather_emails(self, searches, with_data=False):
        emails = []
        for search in searches:
            worker = self.connect()
            result = await self.worker.async_api_request(self.access,
                RequestSearch(context=self.context, terms=search))
            if result and 'emails' in result:
                for metadata in result['emails']:
                    msg = await self.worker.async_api_request(self.access,
                        RequestEmail(
                            metadata=metadata,
                            text=True,
                            data=with_data,
                            username=self.options['--username='][-1],
                            password=self.options['--password='][-1],
                            full_raw=with_data))
                    if msg and 'email' in msg:
                        emails.append(msg['email'])
        return emails

    async def gather_attachments(self, searches):
        atts = []
        for search in searches:
            logging.debug('Unsupported attachment searches: %s' % searches)
            raise Nonsense('FIXME: Searching for attachments does not yet work')
        return atts

    async def render_result(self):
        self.print(await self.render())

    def gather_recipients(self):
        recipients = []
        recipients.extend(self.options.get('--send-to=', []))
        if not recipients:
            recipients.extend(self.options.get('--to=', []))
            recipients.extend(self.options.get('--cc=', []))
            recipients.extend(self.options.get('--bcc=', []))
        return recipients

    async def do_send(self, render=None, recipients=None):
        recipients = recipients or self.gather_recipients()
        if render is None:
            render = await self.render()

        via = (self.options.get('--send-via=') or [None])[-1]
        if not via:
            raise Nonsense('FIXME: Get via from config')

        transcript = []
        def _progress(happy, code, details, message):
            transcript.append((happy, code, details, message))
            return True

        from moggie.util.sendmail import sendmail
        frm = self.options['--from='][0].address
        await sendmail(render, [
                (via, frm, [r.address for r in recipients])
            ],
            progress_callback=_progress)

        self.print_json(transcript)

    async def do_bounce(self):
        recipients = self.gather_recipients()

        data = base64.b64decode(self.forwarding[0]['_RAW'])
        if data.startswith(b'From '):
            data = data[data.index(b'\n')+1:]

        eol = b'\r\n' if (b'\r\n' in data[:1024]) else b'\n'
        sep = eol + eol
        header, body = data.split(sep, 1)

        # According to https://www.rfc-editor.org/rfc/rfc5322.html#page-28
        # we are supposed to generate Resent-* headers and prepend to the
        # message header, with no other changes made. Nice and easy!
        from moggie.email.headers import format_headers
        resent_info = bytes(format_headers({
                'resent-to': recipients,
                'resent-message-id': _make_message_id(),
                'resent-from': self.options['--from='][0]},
            eol=str(eol, 'utf-8')), 'utf-8')[:-len(eol)]

        return await self.do_send(
             render=(resent_info + header + sep + body),
             recipients=recipients)

    async def run(self):
        for target, key, gather, args in (
                (self.replying_to, '--reply=',   self.gather_emails, []),
                (self.forwarding,  '--forward=', self.gather_emails, [True]),
                (self.attachments, '--attach=',  self.gather_attachments, [])):
            if self.options.get(key):
                target.extend(await gather(self.options[key], *args))

        if not self.options.get('--from='):
            self.gather_from()

        if 'bounce' in self.options.get('--forwarding='):
            if (len(self.forwarding) > 1
                    or len(self.options.get('--forward=')) > 1):
                raise Nonsense('Please only bounce/resend one message at a time.')

            await self.do_bounce()
        else:

            if not self.options.get('--to=') and not self.options.get('--cc='):
                self.gather_to_cc()

            if not self.options.get('--subject='):
                self.gather_subject()

            if (self.options.get('--send-to=')
                    or self.options.get('--send-at=')):
                await self.do_send()
            else:
                await self.render_result()
