# A collection of common requests
#
# FIXME: To what degree could these be implemented using the JMAP
#        vocabulary?
# FIXME: There should probably be more sanity checks and validation.
#
import time
import random

from ..email.metadata import Metadata


def _req_id(req_id):
    if not req_id:
        req_id = '%f.%x' % (time.time(), random.randint(0, 0xffffffff))
    return req_id


class RequestBase(dict):
    def update(self, other, req_id=None):
        self['req_id'] = _req_id(req_id)
        dict.update(self, other)
        return self


class RequestPing(RequestBase):
    def __init__(self):
        self.update({'req_type': 'ping', 'ts': int(time.time())})


class RequestSearch(RequestBase):
    def __init__(self, context='', terms='', req_id=None):
        self.update({
            'req_type': 'search',
            'context': context,
            'terms': terms
        }, req_id=req_id)


class RequestOpenPGP(RequestBase):
    def __init__(self, context='', op='', args=[], kwargs={}, req_id=None):
        self.update({
            'req_type': 'openpgp',
            'context': context,
            'op': op,
            'args': args,
            'kwargs': kwargs
        }, req_id=req_id)


class RequestCounts(RequestBase):
    def __init__(self, context='', terms_list=[], req_id=None):
        self.update({
            'req_type': 'counts',
            'context': context,
            'terms_list': terms_list
        }, req_id=req_id)


class RequestTag(RequestBase):
    def __init__(self, context='',
            tag_ops=[], tag_undo_id=None, tag_redo_id=None, undoable=True,
            username=None, password=None,
            req_id=None):
        self.update({
            'req_type': 'tag',
            'context': context,
            'undoable': undoable,
            'tag_undo_id': tag_undo_id,
            'tag_redo_id': tag_redo_id,
            'tag_ops': tag_ops,
            'username': username,
            'password': password
        }, req_id=req_id)


class RequestAutotag(RequestBase):
    def __init__(self, context='', tags=[], search=None, req_id=None):
        self.update({
            'req_type': 'autotag',
            'context': context,
            'tags': tags,
            'search': search
        }, req_id=req_id)


class RequestAutotagTrain(RequestBase):
    def __init__(self,
            context='', tags=[], search=None, compact=False, req_id=None):
        self.update({
            'req_type': 'autotag_train',
            'context': context,
            'tags': tags,
            'search': search,
            'compact': compact
        }, req_id=req_id)


class RequestAutotagClassify(RequestBase):
    def __init__(self,
            context='', tags=[], keywords=None, compact=False, req_id=None):
        self.update({
            'req_type': 'autotag_classify',
            'context': context,
            'tags': tags,
            'keywords': keywords
        }, req_id=req_id)


class RequestPathImport(RequestBase):
    def __init__(self,
            context='', paths=[],
            only_inboxes=False, import_full=False, compact=False,
            req_id=None):
        self.update({
            'req_type': 'path_import',
            'context': context,
            'paths': paths,
            'only_inboxes': only_inboxes,
            'import_full': import_full,
            'compact': compact
        }, req_id=req_id)


class RequestPathPolicy(RequestBase):
    def __init__(self,
            context='', path='',
            label='', account='', watch_policy='', copy_policy='', tags='',
            config_only=False, import_only=False, import_full=False,
            req_id=None):
        self.update({
            'req_type': 'path_policy',
            'context': context,
            'path': path,
            'account': account,
            'watch_policy': watch_policy,
            'copy_policy': copy_policy,
            'tags': tags,
            'config_only': config_only,
            'import_only': import_only,
            'import_full': import_full
        }, req_id=req_id)


class RequestPathPolicies(RequestBase):
    def __init__(self,
            context='', policies=[],
            config_only=False, import_only=False, import_full=False,
            only_inboxes=False, compact=False,
            req_id=None):
        self.update({
            'req_type': 'path_policies',
            'config_only': config_only,
            'import_only': import_only,
            'import_full': import_full,
            'only_inboxes': only_inboxes,
            'compact': compact,
            'context': context,
            'policies': policies
        }, req_id=req_id)


class RequestMailbox(RequestBase):
    def __init__(self, context='',
            mailbox=None, mailboxes=None, limit=50, skip=0, terms=None,
            username=None, password=None, sync_src=None, sync_dest=None,
            req_id=None):
        self.update({
            'req_type': 'mailbox',
            'context': context,
            'mailboxes': [mailbox] if mailbox else mailboxes,
            'terms': terms,
            'sync_src': sync_src,
            'sync_dest': sync_dest,
            'username': username,
            'password': password,
            'limit': limit,
            'skip': skip
        }, req_id=req_id)


class RequestBrowse(RequestBase):
    def __init__(self, context='',
            path='', ifnewer=False,
            username=None, password=None,
            req_id=None):
        self.update({
            'req_type': 'browse',
            'context': context,
            'path': path,
            'ifnewer': ifnewer,
            'username': username,
            'password': password
        }, req_id=req_id)


class RequestEmail(RequestBase):
    def __init__(self,
            metadata=[], text=False, data=False, full_raw=False, parts=None,
            username=None, password=None,
            req_id=None):
        self.update({
            'req_type': 'email',
            'metadata': metadata[:Metadata.OFS_HEADERS],
            'text': text,
            'data': data,
            'parts': parts,
            'full_raw': full_raw,
            'username': username,
            'password': password
        }, req_id=req_id)


class RequestDeleteEmails(RequestBase):
    def __init__(self, context='',
            from_mailboxes=None, metadata_list=[],
            tag_undo_id=None, tag_redo_id=None, undoable=True,
            username=None, password=None,
            req_id=None):
        self.update({
            'req_type': 'delete',
            'context': context,
            'from_mailboxes': from_mailboxes,
            'metadata_list': metadata_list,
            'username': username,
            'password': password
        }, req_id=req_id)


class RequestContexts(RequestBase):
    def __init__(self, req_id=None):
        self.update({
            'req_type': 'contexts',
            # FIXME
        }, req_id=req_id)


class RequestConfigGet(RequestBase):
    DEEP = 'deep'
    def __init__(self,
            which=None,
            urls=False,
            access=False,
            accounts=False,
            identities=False,
            contexts=False,
            req_id=None):
        self.update({
            'req_type': 'config_get',
            'which': which,
            'urls': urls,
            'access': access,
            'identities': identities,
            'accounts': accounts,
            'contexts': contexts,
        }, req_id=req_id)


class RequestConfigSet(RequestBase):
    def __init__(self,
            new=None,
            section=None,
            updates=[],
            req_id=None):
        self.update({
            'req_type': 'config_set',
            'new': new,
            'section': section,
            'updates': updates
        }, req_id=req_id)


class RequestSetSecret(RequestBase):
    def __init__(self,
            key=None, context=None, secret=None, ttl=None, req_id=None):
        self.update({
            'req_type': 'set_secret',
            'context': context,
            'key': key,
            'ttl': ttl,
            'secret': secret,
            'passphrase': passphrase
        }, req_id=req_id)


class RequestUnlock(RequestBase):
    def __init__(self, passphrase=None, req_id=None):
        self.update({
            'req_type': 'unlock',
            'passphrase': passphrase
        }, req_id=req_id)


class RequestChangePassphrase(RequestBase):
    def __init__(self,
            old_passphrase=None,
            new_passphrase=None,
            disconnect=False, req_id=None):
        self.update({
            'req_type': 'change_passphrase',
            'old_passphrase': old_passphrase,
            'new_passphrase': new_passphrase,
            'disconnect': disconnect
        }, req_id=req_id)


class RequestCommand(RequestBase):
    def __init__(self,
            command=None, args=None, username=None, password=None,
            req_id=None):
        self.update({
            'req_type': 'cli:%s' % command,
            'username': username,
            'password': password,
            'args': args
        }, req_id=req_id)

    command = property(lambda s: s['req_type'].split(':', 1)[-1])

    def set_arg(self, name, value):
        if name[:2] != '--':
            name = '--%s' % name
        if name[-1:] != '=':
            name += '='
        args = [a for a in self.get('args', []) if not a.startswith(name)]
        if value is not None:
            args.append('%s%s' % (name, value))
        self['args'] = args


def to_api_request(_input):
    cls = {
         'cli': RequestCommand,
         'tag': RequestTag,
         'autotag': RequestAutotag,
         'autotag_train': RequestAutotagTrain,
         'autotag_classify': RequestAutotagClassify,
         'ping': RequestPing,
         'email': RequestEmail,
         'delete': RequestDeleteEmails,
         'counts': RequestCounts,
         'search': RequestSearch,
         'browse': RequestBrowse,
         'mailbox': RequestMailbox,
         'contexts': RequestContexts,
         'config_set': RequestConfigSet,
         'config_get': RequestConfigGet,
         'path_import': RequestPathImport,
         'path_policy': RequestPathPolicy,
         'path_policies': RequestPathPolicies,
         'openpgp': RequestOpenPGP,
         'unlock': RequestUnlock,
         'change_passphrase': RequestChangePassphrase,
         }.get(_input.get('req_type', '').split(':')[0])
    if cls:
        obj = cls()
        obj.update(_input)
        return obj
    raise KeyError('Unrecognized request: %s' % _input)
