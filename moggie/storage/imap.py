import imaplib
import logging
import socket
import sys
import time

from ..email.metadata import Metadata
from ..email.parsemime import parse_message as ep_parse_message
from ..email.util import quick_msgparse, make_ts_and_Metadata
from ..email.util import mk_packed_idx, unpack_idx
from ..util.dumbcode import *
from ..util.imap import ImapConn
from ..util.mailpile import PleaseUnlockError

from .base import BaseStorage
from .mailboxes import MailboxStorageMixin


class ImapMailbox:
    def __init__(self, conn, path): 
        self.conn = conn
        self.path = path
        self.prefix = None

    def unlock(self, *args, **kwargs):
        return self.conn.unlock(*args, **kwargs)

    def get_prefix(self):
        if self.prefix is None:
            self.prefix = 'imap://%s@%s/%s' % (
                self.conn.username.replace('@', '%40'),
                self.conn.host_port,
                self.path)
        return self.prefix

    def make_path(self, uid):
        return bytes('%s/%x.%x' % (
                self.get_prefix(), self.conn.selected['UIDVALIDITY'], uid
            ), 'utf-8')

    @classmethod
    def path_to_uids(cls, path):
        return (int(u, 16) for u in path.rsplit('/')[-1].split('.', 1))

    def compare_idxs(self, idx1, idx2):
        p1, h1 = unpack_idx(idx1, count=2)
        p2, h2 = unpack_idx(idx2, count=2)
        return (h1 and h2 and (h1 == h2))

    def iter_email_metadata(self, skip=0, ids=None, reverse=False):
        lts = 0
        now = time.time()

        uids = self.conn.uids(self.path, skip=skip)
        if reverse:
            uids = list(reversed(uids))
        uids = uids[skip:]

        batch = 25
        for beg in range(0, len(uids), batch):
            for uid, size, _, msg in self.conn.fetch_metadata(
                    self.path,
                    uids[beg:beg+batch]):
                hend, hdrs = quick_msgparse(msg, 0)
                path = self.make_path(uid)
                lts, md = make_ts_and_Metadata(
                    now, lts, msg[:hend],
                    [Metadata.PTR(Metadata.PTR.IS_IMAP, path, size, uid)],
                    hdrs)
                md[Metadata.OFS_IDX] = mk_packed_idx(
                    hdrs, uid, self.conn.selected['UIDVALIDITY'],
                    count=2, mod=6)
                yield md


class ImapStorage(BaseStorage, MailboxStorageMixin):
    def __init__(self, metadata=None, ask_secret=None, set_secret=None):
        self.metadata = metadata
        self.ask_secret = ask_secret
        self.set_secret = set_secret
        self.conns = {}
        BaseStorage.__init__(self)
        self.dict = None

    def can_handle_ptr(self, ptr):
        return (ptr.ptr_type == Metadata.PTR.IS_IMAP)

    def key_to_uhmm(self, key):
        key = str(key, 'utf-8') if isinstance(key, bytes) else key

        try:
            proto, _, user_host_port, path = key.split('/', 3)
        except ValueError:
            proto, _, user_host_port = key.split('/', 2)
            path = ''
        if proto != 'imap:':
            raise KeyError('Not an IMAP key')

        mailbox, message = None, None
        if path:
            mailbox, message = path, None
            if '/' in path[:-1] and path[-1:] != '/':
                mailbox, message = path.rsplit('/', 1)

        user, host_port = None, user_host_port
        if '@' in user_host_port:
            user, host_port = user_host_port.rsplit('@', 1)
            user = user.replace('%40', '@')

        return user, host_port, mailbox, message

    def get_conn(self,
            key=None, user=None, password=None, host_port=None, auth=False):
        if not (user and host_port):
            user, host_port, mailbox, message = self.key_to_uhmm(key)

        def connect(user, host_port, password, auth):
            logging.info('Connecting to imap://%s@%s%s'
                % (user, host_port, ' (authenticated)' if auth else ''))
            conn = ImapConn(user, host_port) # debug=1
            if auth:
                conn = conn.unlock(user, password)
            return conn

        _id = '%s@%s' % (user, host_port)
        if _id not in self.conns:
            self.conns[_id] = connect(user, host_port, password, auth)

        return self.conns[_id].connect()

    def get_mailbox(self, key):
        try:
            user, host_port, mailbox, message = self.key_to_uhmm(key)
            conn = self.get_conn(user=user, host_port=host_port)
            return ImapMailbox(conn, mailbox)
        except (KeyError, IOError):
            return None

    def __contains__(self, key):
        pass

    def __delitem__(self, *args, **kwargs):
        raise RuntimeError('FIXME: Unimplemented')

    def __setitem__(self, *args, **kwargs):
        raise RuntimeError('FIXME: Unimplemented')

    def length(self, key):
        raise RuntimeError('FIXME: Unimplemented')

    def get(self, *args, **kwargs):
        raise RuntimeError('FIXME: Unimplemented')

    def __getitem__(self, key, *gi_args, **kwargs):
        try:
            username = password = context = secret_ttl = None
            if gi_args:
                username, password, context, secret_ttl = gi_args

            key = dumb_decode(key)
            user, host_port, mailbox, message = self.key_to_uhmm(key)
            conn = self.get_conn(
                host_port=host_port,
                user=(username or user), password=password, auth=True)
            if not message or '.' not in message:
                logging.debug('Invalid path, can only fetch messages')
                raise KeyError

            conn.select(mailbox)  # Raises KeyError on failure

            uidvalidity, uid = ImapMailbox.path_to_uids(message)
            if conn.selected.get('UIDVALIDITY') != uidvalidity:
                logging.debug('UID is obsolete: %s != %s'
                    % (uidvalidity, conn.selected.get('UIDVALIDITY')))
                raise KeyError
 
            for uid, data in conn.fetch_messages(mailbox, [uid]):          
                return data
        except PleaseUnlockError:
            raise
        except KeyError:
            pass
        except IOError as e:
            logging.exception('Getitem is failing: %s' % e)
        raise KeyError(key)

    def info(self,
            key=None, details=False, relpath=None,
            recurse=None, limit=None, skip=0,
            username=None, password=None):
        try:
            user, host_port, mailbox, message = self.key_to_uhmm(key)
            conn = self.get_conn(
                host_port=host_port,
                user=(username or user), password=password, auth=True)
        except PleaseUnlockError:
            raise
        except:
            logging.exception('info(%s) failed' % key)
            return {'path': key, 'exists': False}

        prefix = 'imap://%s@%s/' % (user, host_port)
        info = {
            'src': 'imap',
            'path': (prefix + (('%s/' % mailbox) if mailbox else ''))[:-1]}
        if mailbox:
            try:
                conn.select(mailbox)
                info['exists'] = True
                info['magic'] = ['imap']
            except KeyError:
                info['exists'] = False
        if not details:
            return info

        if details is True or 'contents' in details:
            to_scan = [info]
            seen = set()
            while to_scan:
                contents = []
                scan = to_scan.pop(0)
                path = scan['path'][len(prefix):]
                for subpath, rp, sep, flags in conn.ls(path, recurse=False):
                    if rp in seen:
                        continue

                    i = {'src': 'imap', 'path': prefix+subpath}
                    if '\\NOINFERIORS' not in flags:
                        i['is_dir'] = True
                    if '\\NOSELECT' not in flags:
                        i['magic'] = ['imap']
                    if '\\HASCHILDREN' in flags:
                        i['has_children'] = True

                    seen.add(rp)
                    contents.append(i)
                    if recurse and len(rp.split(sep)) <= recurse:
                        to_scan.append(i)
                if contents:
                    scan['is_dir'] = True
                    scan['contents'] = contents
                    scan['has_children'] = True

        return info


if __name__ == "__main__":
    import sys, getpass
    logging.basicConfig(level=logging.DEBUG)

    if len(sys.argv) > 1:
        uri = sys.argv[1]
        pwd = getpass.getpass(uri+': ')
    else:
        uri = 'imap://bre@mailpile.is/'
        pwd = 'incorrect'

    _is = ImapStorage()
    mbx = _is.get_mailbox(uri)
    print('%s' % mbx)
    mbx.unlock(None, pwd)

    print('Tests passed OK')
