"""
This is a minimal rewrite of the decryption logic from Mailpile v1's
mailpile.crypto.streamer module. It may use a fair bit more RAM, but
is simpler and readable, and might be faster as a result.

It relies on aes_utils.aes_ctr_decrypt being backwards compatible and
will still shell out to openssl's CLI tool as necessary.

Also provided are convenience methods for extracting Mailpile v1's
master encryption key and then using that to dump the Mailpile v1
configuration and metadata files.

This module can be used on its own as a tool for extracting legacy
Mailpile data:

   python3 -m moggie.crypto.mailpilev1 master_key
   python3 -m moggie.crypto.mailpilev1 config
   python3 -m moggie.crypto.mailpilev1 metadata
   python3 -m moggie.crypto.mailpilev1 /path/to/encrypted/file

To bulk-decrypt multiple files and save the output to a directory:

   python3 -m moggie.crypto.mailpilev1 dest:/some/dir /path/to/encr...

All of these commands will prompt you to enter the Mailpile master
password, before attempting decryption.

Just like for Mailpile, the MAILPILE_PROFILE environment variable can
be set to extract data from an alternate Mailpile directory.
"""
import hashlib
import io
import logging
import os
import re
import threading
from base64 import b64decode

import moggie.platforms
from .aes_utils import aes_ctr_decrypt
from .passphrases import *
from ..util.mailpile import PleaseUnlockError, sha512b64
from ..util.safe_popen import Popen, PIPE


PGP_MARKER_DELIM1 = b'-----BEGIN PGP MESSAGE-----'
MEP_MARKER_DELIM1 = b"-----BEGIN MAILPILE ENCRYPTED DATA-----"
MEP_MARKER_DELIM2 = b"-----END MAILPILE ENCRYPTED DATA-----"
MEP_MARKER_SINGLE = b"X-Mailpile-Encrypted-Data:"

MARKERS = (PGP_MARKER_DELIM1, MEP_MARKER_DELIM1, MEP_MARKER_SINGLE)

OPENSSL_COMMAND = moggie.platforms.GetDefaultOpenSSLCommand
OPENSSL_MD_ALG = 'md5'

GNUPG_COMMAND = moggie.platforms.GetDefaultGnuPGCommand

WHITESPACE_RE_B = re.compile(b'\\s+')


def _proc_decrypt(command, secret, ciphertext, _debug):
    if _debug:
        _debug('Running: %s' % command)
    decryptor = Popen(command, stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=0)

    def _read_thread(fd, target, _debug):
        def _reader():
            try:
                target[0] = fd.read()
                if _debug:
                    _debug('>> %s' % target[0])
            finally:
                fd.close()
        thread = threading.Thread(target=_reader)
        thread.daemon = True
        thread.start()
        return thread

    def _write(proc, data, _debug):
        if _debug:
            _debug('<< %s' % data)
        return proc.stdin.write(data)

    err, out = [None], [None]
    err_r = _read_thread(decryptor.stderr, err, _debug)
    out_r = _read_thread(decryptor.stdout, out, _debug)

    _write(decryptor, secret + b'\n', _debug)
    _write(decryptor, ciphertext, _debug)
    decryptor.stdin.close()

    err_r.join()
    out_r.join()

    return out[0], str(err[0], 'utf-8')


def _gnupg_dec(passphrase, ciphertext, _debug):
    command = [
        str(GNUPG_COMMAND(), 'utf-8'),
        '--utf8-strings',
        '--pinentry-mode=loopback',
        '--passphrase-fd=0',
        '--decrypt']
    return _proc_decrypt(command, passphrase, ciphertext, _debug)


def _openssl_dec(cipher, key, ciphertext, _debug):
    command = [
        str(OPENSSL_COMMAND(), 'utf-8'),
        "enc", "-d", "-a", "-%s" % cipher,
        "-pass", "stdin", "-md", OPENSSL_MD_ALG]
    return _proc_decrypt(command, key, ciphertext, _debug)


def _decrypt_chunk(key, data, _debug, _raise, maxbytes):
    eol = b'\r\n' if (b'\r\n' in data) else b'\n'
    eol_pos = data.index(eol+eol)
    head = data[:eol_pos]
    ciphertext = data[eol_pos + len(eol)*2:]
    headers = dict([l.split(': ', 1)
        for l in str(head, 'utf-8').splitlines() if ': ' in l])

    # Get our parameters
    version = headers.get('X-Mailpile-Encrypted-Data', 'v1')
    cipher = headers.get('cipher')
    md5sum = headers.get('md5sum')
    sha256 = headers.get('sha256')
    nonce = bytes(headers.get('nonce', ''), 'utf-8')

    def _ctext():
        nonlocal maxbytes, ciphertext
        if maxbytes:
            # Avoid reading everythig!
            ciphertext = ciphertext[:((maxbytes*4) // 3 + 1024)]
            # A block size of 32 bytes, is a multiple of both 128 and 256 bit.
            # Base64 blocks are 3 bytes per every 4 encoded.
            # So we align to a multiple of 32*3 = 96.
            if (maxbytes % 96):
                maxbytes -= (maxbytes % 96)
                maxbytes += 96
            maxbytes //= 3
            maxbytes *= 4
            return WHITESPACE_RE_B.sub(b'', ciphertext)[:maxbytes]
        else:
            return ciphertext

    mutated_key = sha512b64(key, nonce)[:32].strip()
    if _debug:
        _debug('key=%s\nnonce=%s\nmutated=%s' % (key, nonce, mutated_key))

    if cipher == 'aes-128-ctr':
        md5_key = hashlib.md5(mutated_key).digest()
        plaintext = aes_ctr_decrypt(md5_key, nonce, b64decode(_ctext()))

    elif cipher[:4] == 'aes-':
        plaintext, err = _openssl_dec(cipher, mutated_key, ciphertext, _debug)
        if err and 'WARNING' not in err:
            raise IOError('openssl: %s' % err)

    elif cipher == 'none':
        plaintext = b64decode(_ctext())

    elif cipher == 'broken':
        plaintext = ciphertext

    else:
        raise ValueError('Unsupported cipher: %s' % cipher)

    if md5sum and not maxbytes:
        dighex = hashlib.md5(mutated_key + nonce + plaintext).hexdigest()
        if dighex != md5sum:
            if _debug:
                _debug('Bad decrypt? %s' % plaintext)
            if _raise:
                raise PleaseUnlockError(
                    'MD5 mismatch: %s != %s' % (dighex, md5sum))
            else:
                return ciphertext

    if sha256 and not maxbytes:
        inner_sha = hashlib.sha256(mutated_key + nonce + plaintext).digest()
        dighex = hashlib.sha256(mutated_key + inner_sha).hexdigest()
        if dighex != sha256:
            if _debug:
                _debug('Bad decrypt? %s' % plaintext)
            if _raise:
                raise PleaseUnlockError(
                    'SHA256 mismatch: %s != %s' % (dighex, sha256))
            else:
                return ciphertext

    if maxbytes:
        return plaintext[:maxbytes]
    else:
        return plaintext


def _mailpile1_kdf(data, key):
    import json, sys

    header_data = str(data, 'utf-8').replace('\r', '').split('\n\n')[0]
    header = {}
    for line in header_data.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            header[k.lower()] = v.strip()

    kdf = header.get('kdf', '')
    salt = bytes(header.get('salt', ''), 'utf-8')

    def _to_b64w(s):
        return s.replace(b'/', b'_').replace(b'+', b'-').replace(b'=', b'')

    if kdf.startswith('scrypt '):
        params = json.loads(kdf[7:])
        return _to_b64w(stretch_with_scrypt(key, salt, params))

    elif kdf.startswith('pbkdf2 '):
        params = json.loads(kdf[7:])
        return _to_b64w(stretch_with_pbkdf2(key, salt, params))

    return key


def decrypt_mailpilev1(key, data, _debug=False, _raise=False, maxbytes=None):
    if isinstance(data, str):
        data = bytes(data, 'latin-1')
    elif hasattr(data, 'read'):
        data = data.read()

    # This is our SecurePassphraseStorage support
    if hasattr(key, 'get_passphrase_bytes'):
        key = key.get_passphrase_bytes()

    if data.startswith(PGP_MARKER_DELIM1):
        plaintext, err = _gnupg_dec(_mailpile1_kdf(data, key), data, _debug)
        if maxbytes:
            yield plaintext[:maxbytes]
        else:
            yield plaintext

    elif MEP_MARKER_SINGLE in data[:512]:
        yield _decrypt_chunk(key, data, _debug, _raise, maxbytes)

    elif not data.startswith(MEP_MARKER_DELIM1):
        if _raise:
            raise ValueError('Not Mailpile encrypted')
        else:
            yield data

    else:
        for chunk in data.split(MEP_MARKER_DELIM1):
            if chunk:
                chunk = chunk.strip()
                if chunk.endswith(MEP_MARKER_DELIM2):
                    chunk = chunk[:-len(MEP_MARKER_DELIM2)].rstrip()
                plain = _decrypt_chunk(key, chunk, _debug, _raise, maxbytes)
                yield plain
                if maxbytes:
                    maxbytes -= len(plain)
                    if maxbytes < 128:
                        break


def get_mailpile_key(mailpile_path, passphrase, keydata=None, _debug=False):
    if not keydata:
        if isinstance(mailpile_path, str):
            mailpile_path = bytes(mailpile_path, 'utf-8')
        with open(os.path.join(mailpile_path, b'mailpile.key'), 'rb') as fd:
            keydata = fd.read()

    for master_key in decrypt_mailpilev1(passphrase, keydata, _debug):
        if master_key:
            return master_key

    raise ValueError('Unrecognized key data format')


def get_mailpile_data(mailpile_path, passphrase, filepath,
        filedata=None, keydata=None, _debug=False, _raise=True, _iter=False):
    if isinstance(mailpile_path, str):
        mailpile_path = bytes(mailpile_path, 'utf-8')
    if isinstance(filepath, str):
        filepath = bytes(filepath, 'utf-8')
    if not filedata:
        if not b'/' in filepath:
            filepath = os.path.join(mailpile_path, filepath)
        with open(filepath, 'rb') as fd:
            filedata = fd.read()

    master_key = get_mailpile_key(mailpile_path, passphrase, keydata, _debug)
    if _iter:
        return decrypt_mailpilev1(master_key, filedata,
            _debug=_debug,
            _raise=_raise)
    else:
        return b''.join(decrypt_mailpilev1(master_key, filedata,
            _debug=_debug,
            _raise=_raise))


def get_mailpile_config(mailpile_path, passphrase,
        keydata=None, cfgdata=None, _debug=False):
    return get_mailpile_data(mailpile_path, passphrase, 'mailpile.cfg',
        filedata=cfgdata, keydata=keydata, _debug=_debug)


def get_mailpile_metadata(mailpile_path, passphrase,
        keydata=None, metadata=None, _debug=False, _iter=False):
    return get_mailpile_data(mailpile_path, passphrase, 'mailpile.idx',
        filedata=metadata, keydata=keydata, _debug=_debug, _iter=_iter)


if __name__ == '__main__':
    import sys, getpass
    if len(sys.argv) > 1:
        if 'help' in sys.argv or '-h' in sys.argv or '--help' in sys.argv:
            print(__doc__)
            sys.exit(0)

        mp_base = os.path.expanduser('~/.local/share/Mailpile')
        mp_path = os.path.join(mp_base, os.getenv('MAILPILE_PROFILE', 'default'))
        passphrase = bytes(getpass.getpass('Mailpile passphrase: '), 'utf-8')
        args = list(sys.argv[1:])

        target_dir = None
        def output(path, data):
            if target_dir:
                dpath = os.path.join(target_dir, os.path.basename(path))
                with open(dpath, 'wb') as fd:
                    fd.write(data)
                data = bytes(
                    'Wrote %s (%d bytes)\n' % (dpath, len(data)), 'utf-8')
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.write(b'\n')
            sys.stdout.buffer.flush()

        while args:
            cmd = args.pop(0)
            if cmd == 'master_key':
                output(cmd, get_mailpile_key(mp_path, passphrase))
            elif cmd == 'config':
                output(cmd, get_mailpile_config(mp_path, passphrase))
            elif cmd == 'metadata':
                output(cmd, get_mailpile_metadata(mp_path, passphrase))
            elif cmd.startswith('dest:'):
                target_dir = cmd[5:]
                if not os.path.isdir(target_dir):
                    raise OSError('No such directory: %s' % target_dir)
            elif os.path.exists(cmd):
                output(cmd,
                    get_mailpile_data(mp_path, passphrase, cmd, _raise=False))

        sys.exit(0)

    def _assert(val, want=True, msg='assert'):
        if isinstance(want, bool):
            if (not val) == (not want):
                want = val
        if val != want:
            raise AssertionError('%s(%s==%s)' % (msg, val, want))

    LEGACY_TEST_KEY = b'test key'
    LEGACY_PLAINTEXT = b'Hello world! This is great!\nHooray, lalalalla!\n'
    LEGACY_TEST_1 = b"""\
X-Mailpile-Encrypted-Data: v1
cipher: aes-256-cbc
nonce: SEefbOfc9UQmZeWWGWQMrb0n6czXY2Uv
md5sum: b07d3ed58b79a69ab5496cffcab5d878
From: Mailpile <encrypted@mailpile.is>
Subject: Mailpile encrypted data

U2FsdGVkX18zVuMErdegtGziWDLhSvNRb7YRRxmYKMmygI1H3bp+mXffToii6lGB
Z7Vlo78g20D8NAO6dpJfmA==
"""
    LEGACY_TEST_2 = b"""\
-----BEGIN MAILPILE ENCRYPTED DATA-----
cipher: aes-256-cbc
nonce: SB+fmmM72oFpf/FO4wnaHhFBvhgzpbwW
md5sum: 90dfb2850da49c8a6027415521dadb3c

U2FsdGVkX19U8G7SKp8QygUusdHZThlrLcI04+jZ9U5kwfsw7bJJ2721dwgIpCUh
3wpQjsYtFF2dcKBjrG7xyw==

-----END MAILPILE ENCRYPTED DATA-----
-----BEGIN MAILPILE ENCRYPTED DATA-----
cipher: aes-256-cbc
nonce: SB+fmmM72oFpf/FO4wnaHhFBvhgzpbwW
md5sum: 90dfb2850da49c8a6027415521dadb3c

U2FsdGVkX19U8G7SKp8QygUusdHZThlrLcI04+jZ9U5kwfsw7bJJ2721dwgIpCUh
3wpQjsYtFF2dcKBjrG7xyw==

-----END MAILPILE ENCRYPTED DATA-----
"""
    LEGACY_TEST_3 = b"""\
-----BEGIN MAILPILE ENCRYPTED DATA-----
cipher: aes-128-ctr
nonce: 6b6d6e996fd2e8cab3abd40dcc1d0b05
sha256: 88c8fb55e47768e7edf56feed07ef04e561ff9f28223407856f8c99a8a99f642
X-Mailpile-Encrypted-Data: v2

bPNqyS7Hwr27MNnh/cFCuIckiqMb9VnYCnagmgAokY4tK0LluO1258OxX3pBrWI=

-----END MAILPILE ENCRYPTED DATA-----
"""
    LEGACY_TEST_4 = b"""\
X-Mailpile-Encrypted-Data: v2
cipher: aes-128-ctr
nonce: 7e85e80dd3b73928fbf37598c984cc2c
sha256: 54732e65bb4357ef7827a4fd8e0892fbedde37a6931ed8225eb104a7ccd4e577
From: Mailpile <encrypted@mailpile.is>
Subject: Mailpile encrypted data

5iXLK5HfippM4ckuuEKnvqatniazhJKRRgu82aCMaJmiVHVRyDql5jNYQWEC9Ys=
"""
    GNUPG_KEYDATA_SECRET = b'this is a secret key\n'
    GNUPG_KEYDATA_TEST = b"""\
-----BEGIN PGP MESSAGE-----

jA0ECQMCm+6E6RW9whX/0kkBbtxMwonfalH5ZZimPJIYd/Ji/b4sWya1wrSykkRI
o58wVfYsXFiJaxSRQACC8o6tRJot/+S2nNo992PL7QwzcJsJECQVpc1L
=ebA7
-----END PGP MESSAGE-----
"""
    # If we are keeping passphrases in RAM for extended periods of
    # time, we should use the SecurePassphraseStorage.  Make sure
    # secrets in that format are usable.
    secret = SecurePassphraseStorage(passphrase=LEGACY_TEST_KEY)

    # Test the Mailpile v1 AES encryption, both built-in and openssl based
    for i, o, mb in (
            (LEGACY_TEST_1, LEGACY_PLAINTEXT,      None),
            (LEGACY_TEST_2, LEGACY_PLAINTEXT * 2,  None),
            (LEGACY_TEST_3, LEGACY_PLAINTEXT,      None),
            (LEGACY_TEST_4, LEGACY_PLAINTEXT,      None),
            (LEGACY_TEST_1, LEGACY_PLAINTEXT,      96),
            (LEGACY_TEST_2, LEGACY_PLAINTEXT,      96), # Partial result!
            (LEGACY_TEST_3, LEGACY_PLAINTEXT,      96),
            (LEGACY_TEST_4, LEGACY_PLAINTEXT,      95)):
        # Decryption success test
        c = b''.join(decrypt_mailpilev1(
                        secret, i,
                        _debug=False, _raise=True, maxbytes=mb))
        _assert(c, want=o)

        # Decryption failure test
        if not mb:
          try:
            c = b''.join(decrypt_mailpilev1(b'bad secret', i, _raise=True))
            _assert(b'not reached', False)
          except PleaseUnlockError:
            pass

    # Test our logic for extracting the Mailpile master key
    for i, o in (
            (GNUPG_KEYDATA_TEST, GNUPG_KEYDATA_SECRET),
            ):
        k = get_mailpile_key(None, secret, keydata=i)
        _assert(k, want=o)

    print('Tests passed OK')
