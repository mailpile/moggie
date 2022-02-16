# A collection of common requests
#
# FIXME: These should all be rewritten to actually use JMAP.
# FIXME: There should probably be sanity checks and validation etc.
#
import time

from ..email.metadata import Metadata


class RequestPing(dict):
    def __init__(self):
        self.update({'prototype': 'ping', 'ts': int(time.time())})


class RequestSearch(dict):
    def __init__(self, terms=''):
        self.update({
            'prototype': 'search',
            'terms': terms})


class RequestMailbox(dict):
    def __init__(self, mailbox='', limit=50, skip=0):
        self.update({
            'prototype': 'mailbox',
            'mailbox': mailbox,
            'limit': limit,
            'skip': skip})


class RequestEmail(dict):
    def __init__(self, metadata=[], text=False, data=False):
        self.update({
            'prototype': 'email',
            'metadata': metadata[:Metadata.OFS_HEADERS],
            'text': text,
            'data': data})


class RequestTag(dict):
    def __init__(self, tagname=''):
        self.update({
            'prototype': 'search',
            'terms': 'in:%s' % tagname})
 

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
