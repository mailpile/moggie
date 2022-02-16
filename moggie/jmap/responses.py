# A collection of common responses
#
# FIXME: These should all be rewritten to actually use JMAP.
# FIXME: There should probably be sanity checks and validation etc.
#

class ResponsePing(dict):
    def __init__(self, request):
        self.update({'prototype': 'pong', 'ts': request['ts']})


class ResponseMailbox(dict):
    def __init__(self, request, emails):
        self.update({
            'prototype': 'mailbox',
            'mailbox': request['mailbox'],
            'limit': request['limit'],
            'skip': request['skip'],
            'emails': emails})


class ResponseEmail(dict):
    def __init__(self, request, parsed_email):
        self.update({
            'prototype': 'mailbox',
            'metadata': request['metadata'],
            'email': parsed_email})
