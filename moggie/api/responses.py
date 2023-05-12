# A collection of common responses
#
# FIXME: To what degree could these be implemented using the JMAP
#        vocabulary?
# FIXME: There should probably be more sanity checks and validation
#
import time


class ResponsePing(dict):
    def __init__(self, request):
        self.update({'req_type': 'pong', 'ts': request.get('ts', 0)})


class ResponseNotification(dict):
    def __init__(self, notification):
        self.update(notification)
        self.update({
            'req_type': 'notification',
            'ts': int(time.time())})


class ResponsePleaseUnlock(ResponseNotification):
    def __init__(self, request):
        super().__init__({
            'message': 'App is locked. Please provide a passphrase to unlock.',
            'please_unlock': True,
            'postponed': request})


class ResponseUnlocked(dict):
    def __init__(self, request):
        super().__init__({
            'req_type': 'unlocked',
            'message': 'App unlocked!',
            'ts': int(time.time())})


class ResponseAddToIndex(dict):
    def __init__(self, request, done, total):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'context': request['context'],
            'total': total,
            'done': done})


class ResponseMailbox(dict):
    def __init__(self, request, emails, watched):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'context': request['context'],
            'mailbox': request['mailbox'],
            'limit': request['limit'],
            'skip': request['skip'],
            'watched': watched,
            'emails': emails})


class ResponseBrowse(dict):
    def __init__(self, request, paths):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'context': request['context'],
            'path': request['path'],
            'info': paths})


class ResponseSearch(dict):
    def __init__(self, request, emails, results):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'context': request['context'],
            'terms': request['terms'],
            'limit': request['limit'],
            'skip': request['skip']})
        if emails is not None:
            self['emails'] = emails
        if results is not None:
            self['results'] = results


class ResponseCounts(dict):
    def __init__(self, request, counts):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'context': request['context'],
            'counts': counts})


class ResponseTag(dict):
    def __init__(self, request, results):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'context': request['context'],
            'results': results})


class ResponseEmail(dict):
    def __init__(self, request, parsed_email):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'metadata': request['metadata'],
            'email': parsed_email})


class ResponseConfigGet(dict):
    def __init__(self, request, config_data, error=None):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'config': config_data})
        if error is not None:
            self['error'] = error


class ResponseConfigSet(dict):
    def __init__(self, request, config_data, error=None):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'config': config_data})
        if error is not None:
            self['error'] = error


class ResponseContexts(dict):
    def __init__(self, request, contexts):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'contexts': contexts})


class ResponseCommand(dict):
    def __init__(self, request, mimetype, data):
        self.update({
            'req_type': request['req_type'],
            'req_id': request['req_id'],
            'mimetype': mimetype,
            'data': data})
