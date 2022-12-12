# This is the Mailpile1 compatibility, for importing mail and tags
# from a legacy Mailpile 0.x/1.x installation.
#
# It is possible to either access a WERVD (Mailpile's encrypted Maildir)
# directly, without any tag information, or iterate through Mailpile's entire 
# metadata store, which will generate metadata that delegates to other
# storage formats as appropriate.
#
from urllib.parse import unquote
from configparser import ConfigParser

from ...crypto.mailpilev1 import decrypt_mailpilev1
from ...crypto.mailpilev1 import get_mailpile_config, get_mailpile_metadata
from ...crypto.passphrases import SecurePassphraseStorage
from ...email.metadata import Metadata
from ...email.headers import parse_header
from ...email.util import quick_msgparse, make_ts_and_Metadata

from .maildir import FormatMaildir
from . import tag_path


class FormatMaildirWERVD(FormatMaildir):
    NAME = 'maildir1.wervd'
    TAG = b'm1'
 
    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
        return False

    def unlock(self, username, password):
        # FIXME: Find and decrypt the Mailpile v1 config, derive the WERVD
        #        encryption keys...
        pass

    def __getitem__(self, *args, **kwargs):
        raise IOError('FIXME: Decrypt')

    def __delitem__(self, *args, **kwargs):
        raise IOError('WERVD Maildirs are read-only')

    def __setitem__(self, *args, **kwargs):
        raise IOError('WERVD Maildirs are read-only')

    def get_email_headers(self, sub, fn):
        # FIXME: Only decrypt enough of the message to read the headers
        return self.parent[os.path.join(self.basedir, sub, fn)]


class FormatMailpilev1:
    NAME = 'mailpilev1'
    TAG = b'm1a'

    def __init__(self, parent, path, container):
        self.parent = parent
        if not path:
            path = os.expanduser('~/.local/share/Mailpile/default')
        elif '/' not in path:
            path = os.path.expanduser('~/.local/share/Mailpile/%s' % path)
        self.path = path
        self.passphrase = None
        self.config = None

    @classmethod
    def Magic(cls, parent, key, info=None, is_dir=None):
        if (is_dir
               and os.path.join(key, 'mailpile.idx') in parent
               and os.path.join(key, 'mailpile.cfg') in parent):
           return True
        return False

    def unlock(self, username, password):
        self.passphrase = SecurePassphraseStorage(passphrase=password)
        self.config = get_mailpile_config(self.path, self.passphrase)

        # FIXME: Extract Mailpile's tags from the config

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

    def message_tags(self):
        tag_info = self.tag_info()
        msg_tags = {}
        count = 0
        for chunk in get_mailpile_metadata(
                self.path, self.passphrase, _iter=True):
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

    def iter_email_metadata(self,
            skip=0, iterator=None, username=None, password=None):

        known_emails = {}
        def _to_emails(field):
            return [
                 unquote(str(known_emails[i], 'latin-1'))
                 for i in field.split(',') if i and i in known_emails]

        for chunk in get_mailpile_metadata(
                self.path, self.passphrase, _iter=True):
            print('Got %d bytes of metadata' % len(chunk))
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
    from ..files import FileStorage

    fs = FileStorage()

    if len(sys.argv) > 1:
        profile, op = sys.argv[1:]
        mp = FormatMailpilev1(fs, profile, None)
        mp.unlock(None,
            getpass.getpass('Your Mailpile(%s) passphrase: ' % profile))
        if op == 'tag_infos':
            print('%s' % json.dumps(mp.tag_info(), indent=1))
        elif op == 'message_tags':
            for msgid, tags in mp.message_tags():
                print('%s -- msgid:%s' % (
                    ' '.join('+%s' % t for t in tags),
                    msgid))
        else:
            data = {'error': 'Unknown op: %s' % op}

        sys.exit(0)

    ## Tests follow ##

    fs = FileStorage()
    fs.RegisterFormat(FormatMaildirWERVD)
    fs.RegisterFormat(FormatMailpilev1)

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

