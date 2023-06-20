# Low level commands exposing Moggie's OpenPGP support
#
import io
import logging
import os
import re
import sys
import time

import pgpdump

from moggie.util.dumbcode import *
from moggie.crypto.openpgp.keyinfo import KeyInfo
from .command import Nonsense, CLICommand, AccessConfig


class WorkerEncryptionWrapper:
    def __init__(self, cli_obj):
        from moggie.api.requests import RequestOpenPGP
        self.cli_obj = co = cli_obj
        for op in (
                'get_cert',
                'find_certs',
                'list_certs',
                'get_private_key',
                'find_private_keys',
                'list_private_keys',
                'save_cert',
                'save_private_key',
                'delete_cert',
                'delete_private_key',
                'process_email',
                'list_profiles',
                'generate_key',
                'sign',
                'verify',
                'encrypt',
                'decrypt'):
            def mk_req(_op):
                async def _request(*args, **kwargs):
                    request = RequestOpenPGP(
                        context=co.context,
                        op=_op,
                        args=args,
                        kwargs=kwargs)
                    result = await co.worker.async_api_request(
                        co.access, request)
                    if result.get('error'):
                        logging.error('%s(...): %s' % (_op, result['error']))
                        raise Exception(result['error'])
                    return result['result']
                return _request
            setattr(self, op, mk_req(op))


class CommandOpenPGP(CLICommand):
    """moggie pgp [<command>] [<options> ...]

    Low level commands for interacting with PGP keys, encrypting or
    decrypting.

    ## General options

    These options control the high-level behavior of this command; where
    it loads default settings from, what it does with the message once
    it has been generated, and how the output is formatted:

    %(moggie)s

    ### Examples:

        ...

    ## Known bugs and limitations

    A bunch of the stuff above isn't yet implemented. It's a lot!
    This is a work in progress.

    """
    NAME = 'pgp'
    ROLES = AccessConfig.GRANT_READ
    CONNECT = False    # We manually connect if we need to!
    WEBSOCKET = False
    WEB_EXPOSE = True
    OPTIONS_COMMON = [
        (None, None, 'moggie'),
        ('--context=', ['default'], 'Context to use for default settings'),
        ('--format=',     ['text'], 'X=(text|json|sexp)'),
        ('--stdin=',            [], None), # Allow lots to send stdin (internal)
    ]
    OPTIONS_PGP_SETTINGS = [
        ('--pgp-sop=',          [], '"PGPy" or /path/to/SOP/tool'),
        ('--pgp-key-sources=',  [], 'Ordered list of key stores/sources'),
        ('--pgp-password=',     [], 'Password to use to unlock PGP keys'),
        ('--pgp-htmlwrap=',  ['N'], 'X=(Y|N*), wrap PGP data in HTML'),
    ]
    OPTIONS_SIGNING = [
        (None, None, 'signing'),
        ('--pgp-clearsign=',  ['N'], 'X=(Y|N*), include content in signature'),
        ('--sign-with=',         [], 'Keys or fingerprints to sign with'),
    ]
    OPTIONS_VERIFYING = [
        (None, None, 'verifying'),
        ('--verify-from=',     [], 'Keys or fingerprints to verify with'),
    ]
    OPTIONS_ENCRYPTING = [
        (None, None, 'encrypting'),
        ('--encrypt-to=',       [], 'Keys or fingerprints to encrypt to'),
    ]
    OPTIONS_DECRYPTING = [
        (None, None, 'decrypting'),
        ('--decrypt-with=',     [], 'Keys or fingerprints to decrypt with'),
    ]
    OPTIONS_AUTOCRYPT = [
        (None, None, 'autocrypt'),
        ('--autocrypt-with=',   [], 'Keys/fingerprints to share/decrypt with'),
        ('--autocrypt=',        [], 'X=(N|auto|/path/to/autocrypt/DB)'),
    ]
    OPTIONS = [OPTIONS_COMMON + OPTIONS_PGP_SETTINGS]

    HTML_PKEY_WRAPPER = """\
<html><body><h1>  OpenPGP Private Key  </h1><p>

This is an OpenPGP Private Key. It can be used to generate
digital signatures or decrypt encrypted messages. It should be
kept secret.

</p><pre>\n%s\n</pre></body></html>"""
    HTML_CERT_WRAPPER = """\
<html><body><h1>  OpenPGP Public Certificate  </h1><p>

This is an OpenPGP Public Certificate. It can be used to verify
digital signatures or encrypt messages to the owner.

</p><pre>\n%s\n</pre></body></html>"""
    HTML_ENCRYPTED_WRAPPER = """\
<html><body><h1>  OpenPGP Encrypted Data  </h1><p>

This is an OpenPGP encrypted message or file. If you are in
possession of the right keys, you should be able to decrypt it
using GnuPG or other OpenPGP compatible software.

</p><pre>\n%s\n</pre></body></html>"""
    HTML_SIG_WRAPPER = """\
<html><body><h1>  OpenPGP Digital Signature  </h1><p>

This is a digital signature, which can be used to verify the
authenticity of this message. You can safely discard or ignore
this file if your e-mail software does not support digital
signatures.

</p><pre>\n%s\n</pre></body></html>"""
    CLEAR_SIG_PREFIX = """\
-----BEGIN PGP SIGNED MESSAGE-----
Hash: %s

"""

    def __init__(self, *args, **kwargs):
        self.args = []
        self.sign_with = []
        self.verify_from = []
        self.encrypt_to = []
        self.decrypt_with = []
        super().__init__(*args, **kwargs)

    @classmethod
    def configure_passwords(cls, cli_obj, which=['--pgp-password=']):
        for opt in which:
            for i, v in enumerate(cli_obj.options[opt]):
                if v and v.lower() == 'ask':
                    import getpass
                    prompt = 'Password (%s): ' % opt[2:-1]
                    cli_obj.options[opt][i] = getpass.getpass(prompt)

    @classmethod
    def read_file_or_stdin(cls, cli_obj, path):
        if path == '-':
            if cli_obj.options['--stdin=']:
                return cli_obj.options['--stdin='].pop(0)
            else:
                return str(sys.stdin.buffer.read(), 'utf-8')
        else:
            with open(path, 'r') as fd:
                return fd.read()

    @classmethod
    def load_key_from_file(cls, cli_obj, arg):
        if arg[:1] not in ('.', '/') and arg != '-':
            return None
        try:
            key = cls.pgp_strip(cls.read_file_or_stdin(cli_obj, arg))
            if not key:
                raise Nonsense(
                    'Not an ASCII-armored OpenPGP key: %s' % arg)
            return key
        except (OSError, IOError):
            raise Nonsense('File not found or unreadable: %s' % arg)

    @classmethod
    def configure_keys(cls, cli_obj):
        for arg in (
                '--sign-with=',
                '--verify-from=',
                '--encrypt-to=',
                '--decrypt-with='):
            keep = []
            for i, v in enumerate(cli_obj.options.get(arg) or []):
                v = v.strip()
                if v.startswith('PGP:'):
                    prefix = v[:4]
                    v = v[4:]
                else:
                    prefix = ''

                if cls.pgp_strip(v):
                    pass
                else:
                    key = cls.load_key_from_file(cli_obj, v)
                    if key:
                        cli_obj.options[arg][i] = prefix + key

    @classmethod
    def get_keyids_and_keys(cls, cli_obj, opt,
            dkim=True, pgp=True, extras=[]):
        ids = {'DKIM': [], 'PGP': []}
        for _id in (cli_obj.options[opt] + extras):
            t, i = _id.split(':', 1)
            ids[t.upper()].append(i)
        if not dkim:
            del ids['DKIM']
        if not pgp:
            del ids['PGP']
        return ids

    @classmethod
    def get_keyids_from_data(self, data, private=False):
        ids = []
        if data:
            prefix = 'PGP:@PKEY:' if private else 'PGP:@CERT:'
            try:
                for p in pgpdump.AsciiData(data).packets():
                    if hasattr(p, 'key_id'):
                        ids.append(prefix + str(p.key_id, 'utf-8'))
            except (ValueError, TypeError, IndexError):
                pass
        return ids

    @classmethod
    def get_signing_ids_and_keys(cls, cli_obj):
        return cls.get_keyids_and_keys(cli_obj, '--sign-with=')

    @classmethod
    def get_encrypting_ids_and_keys(cls, cli_obj):
        return cls.get_keyids_and_keys(cli_obj, '--encrypt-to=', dkim=False)

    @classmethod
    def get_verifying_ids_and_keys(cls, cli_obj, data=None):
        ids = cls.get_keyids_from_data(data, private=False)
        return cls.get_keyids_and_keys(cli_obj, '--verify-from=', extras=ids)

    @classmethod
    def get_decrypting_ids_and_keys(cls, cli_obj, data=None):
        ids = cls.get_keyids_from_data(data, private=True)
        return cls.get_keyids_and_keys(
            cli_obj, '--decrypt-with=', dkim=False, extras=ids)

    @classmethod
    def get_encryptor(cls, cli_obj, connect=None, html=None):
        rcpt_ids = cls.get_encrypting_ids_and_keys(cli_obj)
        if not rcpt_ids['PGP']:
            return None, '', '', ''

        if html is None:
            html = cli_obj.options['--pgp-htmlwrap='][-1] in ('Y', 'y', '1')

        pgp_signing_ids = cls.get_signing_ids_and_keys(cli_obj)['PGP']
        sopc, keys = CommandOpenPGP.get_async_sop_and_keystore(cli_obj,
            connect=cls.should_connect(cli_obj, connect))

        async def encryptor(data):
            encrypt_args = {
                'recipients': dict(enumerate(rcpt_ids['PGP']))}
            if pgp_signing_ids:
                encrypt_args['signers'] = dict(enumerate(pgp_signing_ids))
                if cli_obj.options['--pgp-password=']:
                    encrypt_args['keypasswords'] = dict(
                        enumerate(cli_obj.options['--pgp-password=']))
            logging.debug(
                'Encrypting %d bytes with %s' % (len(data), encrypt_args))

            data = bytes(data, 'utf-8') if isinstance(data, str) else data
            encrypt_args['data'] = data

            ctxt = await sopc.encrypt(**encrypt_args)
            ctxt = ctxt if isinstance(ctxt, str) else str(ctxt, 'utf-8')
            if html:
                return cls.HTML_ENCRYPTED_WRAPPER % ctxt.rstrip()
            else:
                return ctxt

        ext = 'html' if html else 'asc'
        return encryptor, 'OpenPGP', ext, 'application/pgp-encrypted'

    @classmethod
    def get_decryptor(cls, cli_obj, connect=None, data=None):
        decrypt_ids = cls.get_decrypting_ids_and_keys(
            cli_obj, data=data)['PGP']
        if not decrypt_ids:
            return None

        pgp_verifying_ids = cls.get_verifying_ids_and_keys(cli_obj)['PGP']
        sopc, keys = CommandOpenPGP.get_async_sop_and_keystore(cli_obj,
            connect=cls.should_connect(cli_obj, connect))

        async def decryptor(data):
            if not decrypt_ids:
                pass  # FIXME: Extract the key ID from the data

            decrypt_args = {
       # FIXME: 'wantsessionkey': True,
                'secretkeys': dict(enumerate(decrypt_ids))}
            if pgp_verifying_ids:
                decrypt_args['signers'] = dict(enumerate(pgp_verifying_ids))
                if cli_obj.options['--pgp-password=']:
                    encrypt_args['keypasswords'] = dict(
                        enumerate(cli_obj.options['--pgp-password=']))
            logging.debug(
                'Decrypting %d bytes with %s' % (len(data), decrypt_args))

            data = bytes(data, 'utf-8') if isinstance(data, str) else data
            decrypt_args['data'] = data
            cleartext, verif, sessionkeys = await sopc.decrypt(**decrypt_args)

            # FIXME: If verification failed due to missing keys, see if we
            #        can find keys and try again?
            return (cleartext, verif, sessionkeys)

        return decryptor

    @classmethod
    def split_clearsigned(cls, data):
        data = bytes(data, 'utf-8') if isinstance(data, str) else data
        data = cls.normalize_text(data)
        dbeg = data.index(b'\r\n\r\n') + 4
        dend = data.index(b'-----BEGIN PGP SIGNATURE-----')
        return (
            cls.dash_unescape(data[dbeg:dend-2]),
            cls.pgp_strip(data[dend:]))

    @classmethod
    def get_verifier(cls, cli_obj, connect=None, sig=None):
        if sig:
            sig = cls.pgp_strip(sig)
        pgp_verifying_ids = cls.get_verifying_ids_and_keys(
            cli_obj, data=sig)['PGP']

        sopc, keys = CommandOpenPGP.get_async_sop_and_keystore(cli_obj,
            connect=cls.should_connect(cli_obj, connect))

        async def verifier(data, sig=None):
            if not sig:
                data, sig = cls.split_clearsigned(data)

            data = bytes(data, 'utf-8') if isinstance(data, str) else data
            verify_args = {
                'sig': sig,
                'signers': dict(enumerate(pgp_verifying_ids))}
            logging.debug(
                'Verifying %d bytes with %s' % (len(data), verify_args))
            if cli_obj.options['--pgp-password=']:
                verify_args['keypasswords'] = dict(
                    enumerate(cli_obj.options['--pgp-password=']))

            verify_args['data'] = data
            return (data, await sopc.verify(**verify_args))

        return verifier

    @classmethod
    async def get_autocrypt_header(cls, cli_obj, addr, prefer_encrypt=None):
        try:
            ac_key = cli_obj.options['--autocrypt-with='][-1]
        except IndexError:
            return None

        if ac_key.startswith('mutual'):
            ac_key = ac_key[8:]
            if prefer_encrypt is None:
                prefer_encrypt=True

        if not ac_key.startswith('-----PGP'):
            sopc, keys = CommandOpenPGP.get_async_sop_and_keystore(cli_obj)
            for key in await keys.find_certs(ac_key):
                ac_key = str(key, 'utf-8') if isinstance(key, bytes) else key
                break

        try:
            key_data = pgpdump.AsciiData(bytes(ac_key, 'utf-8')).data
        except TypeError as e:
            logging.exception('Invalid Autocrypt key data')
            return None

        import base64
        key_data = str(base64.b64encode(key_data), 'utf-8').strip()
        if len(key_data) > 10000:
            # Key too large, Autocrypt specifies a 10KiB limit!
            return None

        attrs = [('addr', addr), ('keydata', key_data)]
        if prefer_encrypt:
            attrs[1:1] = [('prefer-encrypt', 'mutual')]

        return ('autocrypt', attrs)

    @classmethod
    async def autocrypt(cls, cli_obj, sender, recipients):
        if cli_obj.options['--encrypt-to=']:
            return None

        ac_config = cli_obj.options.get('--autocrypt=') or []
        if (not ac_config or ac_config[-1] == 'N'):
            return None

    @classmethod
    def normalize_text(cls, t):
        if isinstance(t, bytes):
            if not t[-1:] == b'\n':
                t += b'\r\n'
            return re.sub(b'[ \t\r]*\n', b'\r\n', t, flags=re.S)
        else:
            if not t[-1:] == '\n':
                t += '\r\n'
            return re.sub('[ \t\r]*\n', '\r\n', t, flags=re.S)

    @classmethod
    def dash_escape(cls, t):
        return re.sub(b'^(-|From )', b'- \\1', t, flags=re.M)

    @classmethod
    def dash_unescape(cls, t):
        return re.sub(b'^- ', b'', t, flags=re.M)

    @classmethod
    def verification_as_dict(cls, v):
        return {
            'when': int(v._when.timestamp()),
            'signing_fpr': v._signing_fpr,
            'primary_fpr': v._primary_fpr}

    @classmethod
    def get_signer(cls, cli_obj, connect=None, html=None, clear=None):
        pgp_signing_ids = cls.get_signing_ids_and_keys(cli_obj)['PGP']
        sopc, keys = CommandOpenPGP.get_async_sop_and_keystore(cli_obj,
            connect=cls.should_connect(cli_obj, connect))

        if html is None:
            html = cli_obj.options['--pgp-htmlwrap='][-1] in ('Y', 'y', '1')
        if clear is None:
            clear = cli_obj.options['--pgp-clearsign='][-1] in ('Y', 'y', '1')
        if clear:
            html = False

        async def signer(data):
            data = bytes(data, 'utf-8') if isinstance(data, str) else data
            if clear:
                # The normalized text always ends in a CRLF, but we do
                # not included it in the signature itself. It gets re-added
                # to the output below.
                data = cls.normalize_text(data)[:-2]

            sign_args = {
                'data': data,
                'wantmicalg': True,
                'signers': dict(enumerate(pgp_signing_ids))}
            if cli_obj.options['--pgp-password=']:
                sign_args['keypasswords'] = dict(
                    enumerate(cli_obj.options['--pgp-password=']))
            sig, micalg = await sopc.sign(**sign_args)
            signature = sig if isinstance(sig, str) else str(sig, 'utf-8')
            if html:
                signature = cls.HTML_SIG_WRAPPER % signature
            elif clear:
                signature = cls.normalize_text(''.join([
                    cls.CLEAR_SIG_PREFIX % (micalg.split('-')[-1].upper(),),
                    str(cls.dash_escape(data), 'utf-8'),
                    '\r\n', signature]))
            return signature, micalg

        ext = 'html' if html else 'asc'
        return signer, 'OpenPGP', ext, 'application/pgp-signature'

    @classmethod
    def should_connect(cls, cli_obj, connect=None):
        if connect is None:
            if cli_obj.options['--context='] != ['default']:
                return True
            sop_cfg = (cli_obj.options.get('--pgp-sop=') or [None])[-1]
            keys_cfg = (cli_obj.options.get('--pgp-key-sources=') or [None])[-1]
            if not (sop_cfg or keys_cfg):
                return True
        return connect

    @classmethod
    def get_async_sop_and_keystore(cls, cli_obj, connect=False):
        sop_cfg = (cli_obj.options.get('--pgp-sop=') or [None])[-1]
        keys_cfg = (cli_obj.options.get('--pgp-key-sources=') or [None])[-1]
        if sop_cfg or keys_cfg:
            from moggie.crypto.openpgp.sop import DEFAULT_SOP_CONFIG, GetSOPClient
            from moggie.crypto.openpgp.keystore import PrioritizedKeyStores
            from moggie.crypto.openpgp.keystore.registry import DEFAULT_KEYSTORES
            from moggie.crypto.openpgp.managers import CachingKeyManager
            from moggie.util.asyncio import AsyncProxyObject
            sc = GetSOPClient(sop_cfg or DEFAULT_SOP_CONFIG)
            ks = PrioritizedKeyStores(keys_cfg or DEFAULT_KEYSTORES)
            km = CachingKeyManager(sc, ks)
            return (
                AsyncProxyObject(sc, arg_filter=km.filter_key_args),
                AsyncProxyObject(ks))

        elif cli_obj.worker or connect:
            if not cli_obj.worker:
                 cli_obj.connect()
            we = WorkerEncryptionWrapper(cli_obj)
            return we, we

        else:
            raise Nonsense('Need a backend worker or explicit PGP settings')

    @classmethod
    def html_wrap_key(cls, key, private):
        if private:
            return cls.HTML_PKEY_WRAPPER % key
        else:
            return cls.HTML_CERT_WRAPPER % key

    @classmethod
    def pgp_strip(self, armor):
        """
        Remove leading and trailing data before/after the first
        PGP ASCII armor marker strings.
        """
        if isinstance(armor, bytes):
            _cr, _lf, _empty, _beg, _end = (
                b'\r', b'\n', b'', b'-----BEGIN PGP ', b'-----END PGP ')
        else:
            _cr, _lf, _empty, _beg, _end = (
                 '\r',  '\n',  '',  '-----BEGIN PGP ',  '-----END PGP ')
        try:
            begin = armor.index(_beg)
            end = armor.index(_end)
            if _lf not in armor[end:]:
                armor += _lf
            end += armor[end:].index(_lf)
            return armor[begin:end].replace(_cr, _empty)
        except ValueError:
            return _empty

    @classmethod
    async def gather_pgp_keys(cls, cli_obj, terms, private_key=None):
        if False:
            yield None

    def configure(self, args):
        #self.preferences = self.cfg.get_preferences(context=self.context)
        args = self.strip_options(args)

        CommandOpenPGP.configure_keys(self)
        CommandOpenPGP.configure_passwords(self)

        self.args = self.configure2(args)
        return self.args

    def configure2(self, args):
        return args

    async def process_key_args(self):
        for arg, priv, target in (
                ('--sign-with=',    True, self.sign_with),
                ('--verify-from=', False, self.verify_from),
                ('--encrypt-to=',  False, self.encrypt_to),
                ('--decrypt-with=', True, self.decrypt_with)):
            for v in self.options.get(arg) or []:
                pgp_key = self.pgp_strip(v)
                if pgp_key:
                    target.append(pgp_key)
                else:
                    async for key in CommandOpenPGP.gather_pgp_keys(
                            self, v, priv):
                        target.append(key)

    def print_results_as_text(self, results):
        return self.print(to_json(results, indent=2))

    def print_results(self, results):
        fmt = self.options['--format='][-1]
        if fmt == 'json':
            self.print_json(results)
        if fmt == 'sexp':
            self.print_sexp(results)
        elif fmt == 'text':
            self.print_results_as_text(results)

    async def run(self):
        await self.process_key_args()

        self.print('FIXME %s' % self.decrypt_with)


class CommandPGPGetKeys(CommandOpenPGP):
    """moggie pgp-get-keys [<options>] <search-terms|fingerprint>

    Search for private keys or public certificates.

    %(OPTIONS)s
    """
    NAME = 'pgp-get-keys'
    OPTIONS = [
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[[
            ('--keystore=',       [], 'Target a specific keystore'),
            ('--private=',     ['N'], 'X=(Y|N*), search for private keys'),
            ('--timeout=',        [], 'X=<seconds>, set a deadline'),
            ('--max-results=',    [], 'X=<limit>, limit number of results'),
            ('--best-first=',  ['Y'], 'X=(Y*|N), sort results by usability'),
            ('--only-usable=', ['Y'], 'X=(Y*|N), omit expired/unusable keys'),
            ('--with-key=',    ['Y'], 'X=(Y*|N), include key material'),
            ('--with-info=',   ['Y'], 'X=(Y*|N), include metadata')]]

    INFO_FMT = '-----BEGIN PGP INFO----\n\n-----END PGP INFO-----\n'

    def print_results_as_text(self, results):
        html_wrap = self.options['--pgp-htmlwrap='][-1] in ('Y', 'y', '1')
        for r in results:
            fpr = r['fingerprint']
            info = ''
            if r['info']:
                info = '\n'.join(
                    '%s: %s' % (k, v) for k, v in r['info'].items()
                    ) + '\n'
            else:
                info = ''
            key = r.get('key', self.INFO_FMT).replace('\r', '')
            if html_wrap:
                pre, post = key.rsplit('\n\n', 1)
            else:
                pre, post = key.split('\n\n', 1)
            self.print(pre + '\n' + info + '\n' + post)

    async def run(self):
        kwa = {}
        if self.options['--keystore=']:
            kwa['which'] = self.options['--keystore='][-1]
        if self.options['--timeout=']:
            deadline = int(self.options['--timeout='][-1])
            kwa['deadline'] = int(time.time() + 0.5 + deadline)
        if self.options['--max-results=']:
            kwa['max_results'] = int(self.options['--max-results='][-1])

        private = self.options['--private='][-1] in ('Y', 'y', '1')
        html_wrap = self.options['--pgp-htmlwrap='][-1] in ('Y', 'y', '1')
        with_info = self.options['--with-info='][-1] in ('Y', 'y', '1')
        with_keys = self.options['--with-key='][-1] in ('Y', 'y', '1')
        best_first = self.options['--best-first='][-1] in ('Y', 'y', '1')
        only_usable = self.options['--only-usable='][-1] in ('Y', 'y', '1')
        sop, keys = self.get_async_sop_and_keystore(self,
            connect=self.should_connect(self))

        if private:
            if with_keys:
                logging.error('FIXME: Require elevated privileges!')
            if self.options['--pgp-password=']:
                kwa['passwords'] = dict(
                    enumerate(self.options['--pgp-password=']))
            list_keys = keys.list_private_keys
            get_key = keys.get_private_key
        else:
            list_keys = keys.list_certs
            get_key = keys.get_cert

        all_keys = [KeyInfo(i).calculate()
            for i in await list_keys(' '.join(self.args), **kwa)]
        if best_first:
            all_keys.sort(key=lambda i: -i['rank'])

        results = []
        for info in all_keys:
             if only_usable and not info['is_usable']:
                 continue
             fpr = info['fingerprint']
             r = {'fingerprint': fpr}
             results.append(r)
             if with_info:
                 r['info'] = info
             if with_keys:
                 if html_wrap:
                     r['key'] = self.html_wrap_key(
                          await get_key(fpr, **kwa), private)
                 else:
                     r['key'] = await get_key(fpr, **kwa)

        return self.print_results(results)


class CommandPGPAddKeys(CommandOpenPGP):
    """moggie pgp-add-keys [<options>] -- <ascii-armored-key|/path/to/key.asc>

    %(OPTIONS)s
    """
    NAME = 'pgp-add-keys'
    OPTIONS = [
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[[
            ('--keystore=', [], 'Target a specific keystore')]]

    def configure2(self, args):
        if not args:
            args = ['-']  # Read stdin by default
        for i, arg in enumerate(args):
            key = self.load_key_from_file(self, arg)
            if not key:
                key = self.pgp_strip(arg)
            if not key:
                raise Nonsense('Not a key: %s' % key)
            args[i] = key
        return args

    async def run(self):
        kwa = {}
        if self.options['--keystore=']:
            kwa['which'] = self.options['--keystore='][-1]

        sop, keys = self.get_async_sop_and_keystore(self,
            connect=self.should_connect(self))

        certs = []
        pkeys = []
        for key in self.args:
            if 'PRIVATE KEY BLOCK' in key:
                certs.append(sop.extract_cert(key))
                pkeys.append(key)
            elif 'PUBLIC KEY BLOCK' in key:
                certs.append(key)
            else:
                raise Nonsense(
                    'Not a key: %s (keys must be ASCII armored)' % key)

        # FIXME: Error handling? Progress reporting?
        for cert in certs:
            await keys.save_cert(cert, **kwa)
        for pkey in pkeys:
            await keys.save_private_key(cert, **kwa)


class CommandPGPDelKeys(CommandOpenPGP):
    """moggie pgp-del-keys [<options>] <fingerprints>

    %(OPTIONS)s
    """
    NAME = 'pgp-del-keys'
    OPTIONS = [
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[[
            ('--keystore=',       [], 'Target a specific keystore'),
            ('--private=',     ['N'], 'X=(Y|N*), also delete private keys')]]

    async def run(self):
        kwa = {}
        if self.options['--keystore=']:
            kwa['which'] = self.options['--keystore='][-1]

        private = self.options['--private='][-1] in ('Y', 'y', 1)

        # FIXME: Error handling? Progress reporting?
        sop, keys = self.get_async_sop_and_keystore(self,
            connect=self.should_connect(self))
        for fpr in self.args:
            await keys.delete_cert(fpr, **kwa)
            if private:
                await keys.delete_private_key(fpr, **kwa)


class CommandPGPSign(CommandOpenPGP):
    """moggie pgp-sign [<options>]

    %(OPTIONS)s
    """
    NAME = 'pgp-sign'
    OPTIONS = ([
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[
            CommandOpenPGP.OPTIONS_SIGNING])

    def configure2(self, args):
        if not args:
            args = ['-']
        self.signing = []
        for i, arg in enumerate(args):
            self.signing.append(self.read_file_or_stdin(self, arg))
        return args

    def print_results_as_text(self, results):
        for result in results:
            self.print(result['signature'])

    async def run(self):
        signer, sname, sext, mimetype = self.get_signer(self)

        results = []
        for data in self.signing:
            sig, micalg = await signer(data)
            results.append({
                'mimetype': mimetype,
                'filename': '%s-signature.%s' % (sname, sext),
                'signature': sig,
                'micalg': micalg})

        self.print_results(results)


class CommandPGPEncrypt(CommandOpenPGP):
    """moggie pgp-encrypt [<options>] ...

    %(OPTIONS)s
    """
    NAME = 'pgp-encrypt'
    OPTIONS = ([
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[
            CommandOpenPGP.OPTIONS_SIGNING +
            CommandOpenPGP.OPTIONS_ENCRYPTING])

    def configure2(self, args):
        if not args:
            args = ['-']
        self.encrypting = []
        for i, arg in enumerate(args):
            self.encrypting.append(self.read_file_or_stdin(self, arg))
        return args

    def print_results_as_text(self, results):
        for result in results:
            self.print(result['ciphertext'])

    async def run(self):
        encryptor, ename, cext, mimetype = self.get_encryptor(self)

        results = []
        for data in self.encrypting:
            ctxt = await encryptor(data)
            results.append({
                'mimetype': mimetype,
                'filename': '%s-encrypted-data.%s' % (ename, cext),
                'ciphertext': ctxt})

        self.print_results(results)


class CommandPGPDecrypt(CommandOpenPGP):
    """moggie pgp-decrypt [<options>]

    %(OPTIONS)s
    """
    NAME = 'pgp-decrypt'
    OPTIONS = ([
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[
            CommandOpenPGP.OPTIONS_VERIFYING +
            CommandOpenPGP.OPTIONS_DECRYPTING])

    def configure2(self, args):
        if not args:
            args = ['-']
        self.decrypting = []
        for i, arg in enumerate(args):
            self.decrypting.append(self.read_file_or_stdin(self, arg))
        return args

    def print_results_as_text(self, results):
        for result in results:
            self.print(str(result['cleartext'], 'utf-8'))

    async def run(self):
        decryptor = self.get_decryptor(self)

        results = []
        for data in self.decrypting:
            cleartext, verifications, sessionkeys = await decryptor(data)
            results.append({
                'cleartext': cleartext,
                'verifications': [self.verification_as_dict(v)
                    for v in verifications],
                'sessionkeys': sessionkeys})

        self.print_results(results)


class CommandPGPVerify(CommandOpenPGP):
    """moggie pgp-verify [<options>]

    %(OPTIONS)s
    """
    NAME = 'pgp-verify'
    OPTIONS = ([
            CommandOpenPGP.OPTIONS_COMMON +
            CommandOpenPGP.OPTIONS_PGP_SETTINGS
        ]+[
            CommandOpenPGP.OPTIONS_VERIFYING])

    def configure2(self, args):
        if not args:
            args = ['-']
        self.verifying = []
        for i, arg in enumerate(args):
            self.verifying.append(self.read_file_or_stdin(self, arg))
        return args

    def print_results_as_text(self, results):
        for text, verifications in results:
            self.print('# verifications_1')
            self.print('\n'.join(str(v) for v in verifications))

    def print_json(self, results):
        return super().print_json([
                {('verifications_%s' % (i+1)): [self.verification_as_dict(v)
                    for v in verifications]}
            for i, (text, verifications) in enumerate(results)])

    async def run(self):
        verifier = self.get_verifier(self)

        results = []
        for data in self.verifying:
            text, verifications = await verifier(data)
            results.append((text, verifications))

        self.print_results(results)
