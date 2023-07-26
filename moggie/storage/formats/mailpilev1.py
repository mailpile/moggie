# This is the Mailpile1 compatibility, for importing mail and tags
# from a legacy Mailpile 0.x/1.x installation.
#
import logging
import os
from urllib.parse import unquote, quote
from configparser import ConfigParser

import moggie.crypto.mailpilev1 as mailpilev1
from ...crypto.mailpilev1 import get_mailpile_config, get_mailpile_metadata
from ...crypto.passphrases import SecurePassphraseStorage
from ...email.metadata import Metadata
from ...email.headers import parse_header
from ...email.util import quick_msgparse, make_ts_and_Metadata
from ...util.mailpile import PleaseUnlockError, tag_quote, tag_unquote

from .maildir import FormatMaildir
from . import tag_path


MASTER_KEY_CACHE = {}


def get_master_key(fs, path, password):
    global MASTER_KEY_CACHE
    path = bytes(path, 'utf-8') if isinstance(path, str) else path
    path_parts = path.split(b'/')

    mailpile_dir = None
    while path_parts and path_parts[-1]:
        _dir = b'/'.join(path_parts)
        if b'/'.join([_dir, b'mailpile.key']) in fs:
            mailpile_dir = _dir
            break
        path_parts.pop(-1)
    if not mailpile_dir:
        raise OSError('file not found: mailpile.key')

    logging.debug('Loading Mailpile master key from %s' % mailpile_dir)
    if mailpile_dir in MASTER_KEY_CACHE:
        master_key = MASTER_KEY_CACHE[mailpile_dir]

    else:
        if not password:
            raise PleaseUnlockError(
                'Need passphrase to unlock Mailpile at %s' % mailpile_dir,
                resource=mailpile_dir, username=False)

        try:
            if not isinstance(password, bytes):
                password = bytes(password, 'utf-8')
            master_key = mailpilev1.get_mailpile_key(mailpile_dir, password)
            if not master_key:
                raise ValueError('No key')

            master_key = SecurePassphraseStorage(passphrase=master_key)
            MASTER_KEY_CACHE[mailpile_dir] = master_key

            logging.debug('Loaded Mailpile master key from %s' % mailpile_dir)
        except (PleaseUnlockError, ValueError) as e:
            raise PleaseUnlockError(
                'Password incorrect for %s' % mailpile_dir,
                resource=mailpile_dir, username=False)

    return mailpile_dir, master_key


class FormatMaildirWERVD(FormatMaildir):
    NAME = 'maildir1.wervd'
    TAG = b'm1'

    # Make sure the Magic method checks for wervd.ver.
    MAGIC_CHECKS = (b'cur', b'new', b'wervd.ver')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.master_key = None
        self.resource = None

    def unlock(self, ignored_username, password, ask_key=None, set_key=None):
        if not self.master_key:
            master_key = None
            try:
                resource, master_key = self._get_master_key(password)
                if set_key is not None:
                    set_key(resource, self.master_key)
            except PleaseUnlockError as pue:
                if ask_key is not None:
                    master_key = ask_key(pue.resource)
                if not master_key:
                    raise pue
            self.master_key = master_key
        return self

    def relock(self):
        global MASTER_KEY_CACHE
        MASTER_KEY_CACHE = {}
        self.master_key = None

    def __delitem__(self, *args, **kwargs):
        raise IOError('WERVD Maildirs are read-only')

    def __setitem__(self, *args, **kwargs):
        raise IOError('WERVD Maildirs are read-only')

    def _get_master_key(self, password=None):
        if self.master_key is None:
            self.resource, self.master_key = get_master_key(
                self.parent, self.path[0], password)
        return self.resource, self.master_key

    def _decrypt(self, ciphertext, maxbytes):
        plaintext = ciphertext
        for marker in mailpilev1.MARKERS:
            if marker in ciphertext[:512].split(b'\r\n', 1)[0]:
                plaintext = b''.join(mailpilev1.decrypt_mailpilev1(
                    self._get_master_key()[1],
                    ciphertext,
                    maxbytes=maxbytes,
                    _raise=True))
                break
        if maxbytes:
            return plaintext[:maxbytes]
        return plaintext

    def __getitem__(self, path, *args, **kwargs):
        ciphertext = super().__getitem__(path, *args, **kwargs)
        try:
            return self._decrypt(ciphertext, None)
        except Exception as e:
            logging.exception('Failed to decrypt %s' % path)
            raise

    def get_email_headers(self, sub, fn):
        path = os.path.join(self.basedir, sub, fn)
        ciphertext = self.parent[path]
        try:
            return self._decrypt(ciphertext, 4096)
        except Exception as e:
            logging.exception('Failed to decrypt %s' % path)
            raise


class FormatMailpilev1:
    NAME = 'mailpilev1'
    TAG = b'm1a'

    def __init__(self, parent, path, container):
        self.parent = parent
        if not path:
            path = os.expanduser('~/.local/share/Mailpile/default')
        elif '/' not in path:
            path = os.path.expanduser('~/.local/share/Mailpile/%s' % path)
        self.path = [path]
        self.passphrase = None
        self.config = None

    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
        if (is_dir
               and os.path.join(key, 'mailpile.idx') in parent
               and os.path.join(key, 'mailpile.cfg') in parent):
           return True
        return False

    def unlock(self, ignored_username, password, ask_key=None, set_key=None):
        if not self.passphrase:
            if not isinstance(password, bytes):
                password = bytes(password, 'utf-8')
            path = self.path[0]
            self.passphrase = SecurePassphraseStorage(passphrase=password)
            _, self.master_key = get_master_key(self.parent, path, password)
            self.config = get_mailpile_config(path, password)
        return self

    def relock(self):
        global MASTER_KEY_CACHE
        MASTER_KEY_CACHE = {}
        self.passphrase = None
        self.master_key = None
        self.config = None

    def __getitem__(self, *args, **kwargs):
        raise IOError('Mailpile v1 is metadata only')

    def __delitem__(self, *args, **kwargs):
        raise IOError('Mailpile v1 data is read-only')

    def __setitem__(self, *args, **kwargs):
        raise IOError('Mailpile v1 data is read-only')

    def tag_info(self):
        tags = {}
        lines = self.config.splitlines()
        while lines:
            if lines[0].startswith(b'[config/tags/'):
                tag_tid = lines.pop(0).split(b':', 1)[0].split(b'/')[-1]
                taginfo = {}
                tags[str(tag_tid, 'utf-8')] = taginfo
                while lines and lines[0].strip():
                    ti = str(lines.pop(0), 'utf-8').rsplit(';', 1)[0].strip()
                    k, v = ti.split(' = ', 1)
                    if k[0] == ';':
                        k = k[1:]
                    if v.startswith('%C0'):
                        v = unquote(v[3:])
                    taginfo[k] = v
            else:
                lines.pop(0)
        return tags

    def slugs_as_keys(self, ti):
        tags = {}
        for key, info in ti.items():
            info['mp_key'] = key
            tags[info['slug']] = info
            if info.get('parent'):
                info['parent'] = ti[info['parent']]['slug']
        return tags

    def message_tags(self):
        tag_info = self.tag_info()
        msg_tags = {}
        count = 0
        for chunk in get_mailpile_metadata(
                self.path[0], self.passphrase, _iter=True):
            chunk = chunk.splitlines()
            for line in chunk:
                if line and line[:1] not in (b'#', b'@'):
                    try:
                        fields = line.split(b'\t')
                        if fields[1] and fields[2]:
                            msgid_hash = fields[2]
                            tags = str(fields[10], 'utf-8').split(',')
                            if tags and tags[0]:
                                yield (
                                    str(msgid_hash, 'utf-8'),
                                    [tag_info.get(t, {'slug': t})['slug']
                                     for t in tags])
                    except (KeyError, ValueError):
                        pass

    def iter_email_metadata(self, skip=0, ids=None, reverse=False):
        if reverse:
            result = reversed(list(self.iter_email_metadata(skip=0, ids=ids)))
            if skip:
                result = list(result)[skip:]
            return result

        known_emails = {}
        def _to_emails(field):
            return [
                 unquote(str(known_emails[i], 'latin-1'))
                 for i in field.split(',') if i and i in known_emails]

        for chunk in get_mailpile_metadata(
                self.path[0], self.passphrase, _iter=True):
#           print('Got %d bytes of metadata' % len(chunk))
            chunk = chunk.splitlines()
            for line in chunk:
                if line[:1] == b'#':
                    pass
                elif line[:1] == b'@':
                    _id, email = line.split(b'\t', 1)
                    known_emails[str(_id[1:], 'latin-1')] = email
                else:
                    # Fields are:
                    #   0..3  mid, ptrs, id, ts
                    #   4..6  from, to, cc
                    #      7  size
                    #      8  subject
                    #      9  snippet
                    #     10  tags
                    #     11  replies
                    #     12  thread ID
                    #
                    fields = str(line, 'utf-8').split('\t')
                    if fields[1] and fields[2]:
                        fields[1] = fields[1].split(',')
                        fields[5] = _to_emails(fields[5])
                        fields[6] = _to_emails(fields[6])
                        fields[10] = fields[10].split(',')
                        fields[11] = fields[11].split(',')
                        print('%s' % fields)
            yield None


if __name__ == "__main__":
    import os, sys, getpass, time, json
    from ...util.dumbcode import dumb_decode
    from ..files import FileStorage

    fs = FileStorage()
    fs.RegisterFormat(FormatMaildirWERVD)
    fs.RegisterFormat(FormatMailpilev1)

    if len(sys.argv) > 1:
        profile, op = sys.argv[1:3]

        try:
            mp = FormatMailpilev1(fs, profile, None)
            mp.unlock(None,
                getpass.getpass('Your Mailpile(%s) passphrase: ' % profile))
        except PleaseUnlockError as pue:
            print('Unlock(%s) failed: %s' % (pue.resource, pue))
            sys.exit(1)

        if op == 'tag_infos':
            print('%s' % json.dumps(mp.tag_info(), indent=1))

        elif op == 'tag_export':
            tfilter = sys.argv[3:]
            skiplist = [t[1:] for t in tfilter if t[:1] == '-']
            droplist = [t[1:] for t in tfilter if t[:1] == '_']
            for msgid, tags in mp.message_tags():
                drop = [t for t in tags if t in droplist]
                tags = [t for t in tags if t not in skiplist]
                if tags and not drop:
                    print('%-31s -- msgid:%s' % (
                        ' '.join('+%s' % tag_quote(t) for t in tags),
                        msgid))
            for slug, info in sorted(mp.slugs_as_keys(mp.tag_info()).items()):
                if slug not in skiplist:
                    print('+%-30s -- META=%s' % (
                       tag_quote(slug), json.dumps(info)))

        elif os.path.isdir(op):
            op = bytes(op, 'utf-8')
            for md in FormatMaildirWERVD(fs, [op], None).iter_email_metadata():
                print('Subject: %s' % md.get_raw_header('Subject'))

        else:
            raise Exception('Error: unknown op: %s' % op)

        sys.exit(0)

    ## Tests follow ##

    assert(FormatMaildir.Magic(fs, b'/tmp/maildir-test', None, is_dir=True))

    bc = FormatMaildir(fs, [b'/tmp/maildir-test'], None)
    fn = bc.append(b'Hello world')
    assert(b'[md:' in fn)
    assert(fn in fs)
    assert(fs[fn][:] == b'Hello world')
    assert(len(list(bc.keys())) == 1)
    del fs[fn]
    assert(fn not in fs)
    assert(len(list(bc.keys())) == 0)

    print('Tests passed OK')

    for path in sys.argv[1:]:
        path = bytes(path, 'utf-8')
        md = FormatMaildir(fs, [path], None)
        print('=== %s (%d) ===' % (path, len(md)))
        print('%s' % '\n'.join('%s' % m for m in md.iter_email_metadata()))
        print('=== %s (%d) ===' % (path, len(md)))

