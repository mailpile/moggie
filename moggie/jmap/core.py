# These are the JMAP Core objects, as described in RFC8620.
#
# Link: https://datatracker.ietf.org/doc/html/rfc8620
#
from .helpers import _dict_helper


class JMAPSessionResource(_dict_helper):
    CAPABILITIES_CORE     = 'urn:ietf:params:jmap:core'
    CAPABILITIES_MAIL     = 'urn:ietf:params:jmap:mail'
    CAPABILITIES_CONTACTS = 'urn:ietf:params:jmap:contacts'
 
    _CC = 'capabilities/' + CAPABILITIES_CORE + '/'
    ATTRS = {
        # Core Capabilities, these are required so we set defaults
        'maxSizeUpload':         (int, 50000000, _CC+ 'maxSizeUpload'),
        'maxConcurrentUpload':   (int,        4, _CC+ 'maxConcurrentUpload'),
        'maxSizeRequest':        (int, 10000000, _CC+ 'maxSizeRequest'),
        'maxConcurrentRequests': (int,        4, _CC+ 'maxConcurrentRequests'),
        'maxCallsInRequest':     (int,       16, _CC+ 'maxCallsInRequest'),
        'maxObjectsInGet':       (int,      500, _CC+ 'maxObjectsInGet'),
        'maxObjectsInSet':       (int,      500, _CC+ 'maxObjectsInSet'),
        'collationAlgorithms':   (list,      [], _CC+ 'collationAlgorithms'),
        # User data
        'accounts':              (dict,      {}, 'accounts'),
        'primaryAccounts':       (dict,      {}, 'primaryAccounts'),
        'username':              (str,           'username'),
        'state':                 (str,           'state'),
        # URLs
        'apiUrl':                (str,           'apiUrl'),
        'downloadUrl':           (str,           'downloadUrl'),
        'uploadUrl':             (str,           'uploadUrl'),
        'eventSourceUrl':        (str,           'eventSourceUrl')}


if __name__ == '__main__':
    class TestDict(_dict_helper):
        ATTRS = {
            'blank': (str, 'blank/blank/blank'),
            'testing': (str, '0', 'wiggle/test'),
            'besting': (int, '0', 'wiggle/best')}

    d = TestDict({'wiggle': {'test': 123}})
    assert(d.besting == 0)
    d.besting = '12345'
    assert(d.testing == '123')
    assert(d.besting == 12345)
    try:
        assert(d.blank is not None)
        assert(not 'reached')
    except AttributeError:
        pass
    d.blank = 'okay'
    assert(d.blank == 'okay')

    try:
        JMAPSessionResource({}, _validate=True)
        assert(not 'reached')
    except ValueError:
        pass

    jsr = JMAPSessionResource()
    assert(jsr.maxSizeUpload  >= 50000000)
    assert(jsr.maxSizeRequest >= 10000000)
    jsr.maxSizeUpload = 1024
    jsr.username = 'bre'
    jsr.state = '0'
    print(jsr)

    jsr2 = JMAPSessionResource(jsr, _validate=True)
    assert(jsr2.maxSizeUpload >= 1024)
