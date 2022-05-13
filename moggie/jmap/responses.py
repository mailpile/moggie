# A collection of common responses
#
# FIXME: These should all be rewritten to actually use JMAP.
# FIXME: There should probably be sanity checks and validation etc.
#
import time


class ResponsePing(dict):
    def __init__(self, request):
        self.update({'prototype': 'pong', 'ts': request.get('ts', 0)})


class ResponseNotification(dict):
    def __init__(self, notification):
        self.update(notification)
        self.update({
            'prototype': 'notification',
            'ts': int(time.time())})


class ResponseAddToIndex(dict):
    def __init__(self, request, done, total):
        self.update({
            'prototype': 'add_to_index',
            'context': request['context'],
            'req_id': request['req_id'],
            'total': total,
            'done': done
        })


class ResponseMailbox(dict):
    def __init__(self, request, emails, watched):
        self.update({
            'prototype': 'mailbox',
            'req_id': request['req_id'],
            'context': request['context'],
            'mailbox': request['mailbox'],
            'limit': request['limit'],
            'skip': request['skip'],
            'watched': watched,
            'emails': emails})


class ResponseSearch(dict):
    def __init__(self, request, emails):
        self.update({
            'prototype': 'search',
            'req_id': request['req_id'],
            'context': request['context'],
            'terms': request['terms'],
            'limit': request['limit'],
            'skip': request['skip'],
            'emails': emails})


class ResponseCounts(dict):
    def __init__(self, request, counts):
        self.update({
            'prototype': 'counts',
            'req_id': request['req_id'],
            'context': request['context'],
            'counts':counts})


class ResponseEmail(dict):
    def __init__(self, request, parsed_email):
        self.update({
            'prototype': 'email',
            'req_id': request['req_id'],
            'metadata': request['metadata'],
            'email': parsed_email})


class ResponseContexts(dict):
    def __init__(self, request, contexts):
        self.update({
            'prototype': 'contexts',
            'req_id': request['req_id'],
            'contexts': contexts})

