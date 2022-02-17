# A collection of common requests
#
# FIXME: These should all be rewritten to actually use JMAP.
# FIXME: There should probably be sanity checks and validation etc.
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


class RequestPing(RequestBase):
    def __init__(self):
        self.update({'prototype': 'ping', 'ts': int(time.time())})


class RequestSearch(RequestBase):
    def __init__(self, terms='', req_id=None):
        self.update({
            'prototype': 'search',
            'terms': terms
        }, req_id=req_id)


class RequestMailbox(RequestBase):
    def __init__(self, mailbox='', limit=50, skip=0, req_id=None):
        self.update({
            'prototype': 'mailbox',
            'mailbox': mailbox,
            'limit': limit,
            'skip': skip
        }, req_id=req_id)


class RequestEmail(RequestBase):
    def __init__(self, metadata=[], text=False, data=False, req_id=None):
        self.update({
            'prototype': 'email',
            'metadata': metadata[:Metadata.OFS_HEADERS],
            'text': text,
            'data': data
        }, req_id=req_id)


class RequestTag(RequestBase):
    def __init__(self, tagname='', req_id=None):
        self.update({
            'prototype': 'search',
            'terms': 'in:%s' % tagname
        }, req_id=req_id)
 

def to_jmap_request(_input):
    cls = {
         'ping': RequestPing,
         'email': RequestEmail,
         'search': RequestSearch,
         'mailbox': RequestMailbox,
         }.get(_input.get('prototype', ''))
    if cls:
        obj = cls()
        obj.update(_input)
        return obj
    raise KeyError('Unrecognized request: %s' % _input)
