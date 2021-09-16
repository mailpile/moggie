"""
Helper functions related to our remote-assisted password recovery scheme.
"""
import binascii
import os
import re
import struct
import time

from ..util.rpc import JsonRpcClient
from ..util.dumbcode import dumb_encode_bin, dumb_decode
from .aes_utils import make_aes_key


VALID_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s\.@]+$')
VALID_CODE_RE = re.compile(r'^\d+[\d-]+\d+$')


def generate_recovery_code(groups=7, length=6):
    """
    An algorithm for generating unbiased random groups of decimal
    numbers. Simplistic for clarity!

    Generates codes with about 139 bits of entropy by default. Not
    at all human friendly, but these ARE keys to the kingdom.
    """
    assert(0 < length < 8)
    g = [''] * groups
    for i in range(0, groups):
        rand_int = struct.unpack('I', os.urandom(4))[0]
        for d in range(0, length):
            g[i] += '%d' % (rand_int % 10,)
            rand_int //= 10
    return '-'.join(g)


def combine_recovery_codes(c1, c2):
    """
    Combine two codes into one.
    """
    try:
        assert(len(c1) == len(c2))
        c3 = ''
        for i in range(0, len(c1)):
            if (c1[i] == '-'):
                c3 += '-'
            elif (c1[i] != '-') and (c2[i] != '-'):
                c3 += '%d' % ((int(c1[i]) + int(c2[i])) % 10)
            else:
                raise ValueError()
        return c3
    except (AssertionError, ValueError):
        raise ValueError('Code groups do not match')


class RecoverySvc(JsonRpcClient):
    def register(self, hint, passcode, contacts):
        return self.call('register',
            hint=hint,
            passcode=passcode,
            contacts=contacts)

    def request_recovery(self, reset_code, recovery_id):
        return self.call('recover',
            reset_code=reset_code,
            recovery_id=recovery_id)

    def get_code(self, reset_code, recovery_id, temporary_code):
        return self.call('code',
            temporary_code=temporary_code,
            reset_code=reset_code,
            recovery_id=recovery_id)


class RecoverableData:
    KEYS = {
        'comment': str,
        'passcode_a': str,
        'encrypted_data': bytes,
        'expires': int,
        'reset_code': str,
        'recovery_id': int,
        'recovery_svc': str}

    def __init__(self, data_dict):
        # Certain fields are required, others are simply ignored.
        # Rraise KeyError, TypeError or ValueError for obviously bad data.
        for k in self.KEYS:
            if not isinstance(data_dict[k], self.KEYS[k]):
                raise TypeError(
                    'Bad RecoveryData, %s is %s' % (k, type(data_dict[k])))
        if not re.match(VALID_CODE_RE, data_dict['passcode_a']):
            raise ValueError('Invalid passcode_a')
        self.data = data_dict

    def save_to_config(self, app_config, section_id):
        section = app_config[section_id]
        recovery_id = None
        for counter in range(0, 100):
            if ('recovery.%d.recovery_svc' % counter) not in section:
                recovery_id = 'recovery.%d.' % counter
                break
        if recovery_id is None:
            raise ValueError('Failed to choose recovery ID')
        for k in self.KEYS:
            if isinstance(self.data[k], bytes):
                section[recovery_id + k] = str(binascii.b2a_base64(
                    self.data[k], newline=False), 'latin-1')
            else:
                section[recovery_id + k] = str(self.data[k])

    @classmethod
    def ForData(cls, secret_data, recovery_svc, hint, contacts):
        passcode_a = generate_recovery_code()
        passcode_b = generate_recovery_code()

        # The Recovery Service stores passcode_b for us, to release only
        # when users pass 2fa (show valid reset_code and temporary_code).
        reg = recovery_svc.register(hint, passcode_b, contacts)

        aes_key = make_aes_key(
            combine_recovery_codes(passcode_a, passcode_b).encode('latin-1'))
        aes_key_iv = (aes_key, b'\0' * 16)
        encrypted_data = dumb_encode_bin(secret_data, aes_key_iv=aes_key_iv)

        return cls({
             'comment': hint,
             'passcode_a': passcode_a,
             'encrypted_data': encrypted_data,
             'expires': int(reg['expires']),
             'reset_code': reg['reset_code'],
             'recovery_id': int(reg['id']),
             'recovery_svc': recovery_svc.url})


if __name__ == '__main__':
    import sys

    rc1 = generate_recovery_code(4, 4)
    rc2 = generate_recovery_code(4, 4)
    rc = combine_recovery_codes(rc1, rc2)

    try:
        combine_recovery_codes(rc1.replace('-', '0'), rc2)
        assert(not 'reached')
    except ValueError:
        pass

    try:
        combine_recovery_codes(rc1, rc2[1:])
        assert(not 'reached')
    except ValueError:
        pass

    assert(combine_recovery_codes(rc2, rc1) == rc)
    assert(re.match(VALID_CODE_RE, rc1))
    assert(re.match(VALID_CODE_RE, rc2))
    assert(re.match(VALID_CODE_RE, rc))

    class MockRecoverySvc(RecoverySvc):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.call = self.mock_call

        def mock_call(self, method, **kwargs):
            if method == 'register':
                return {
                    'id': 1234,
                    'expires': int(time.time()) + 600,
                    'reset_code': '1234-1234'}
            if method == 'recover':
                return {
                    'expires': int(time.time()) + 600,
                    'sent_to': ['br*@kl***.net']}
            if method == 'code':
                return {'passcode': '1234'}

    if len(sys.argv) <= 1:
        rs = MockRecoverySvc('http://localhost/recovery_svc/')
    else:
        rs = RecoverySvc(sys.argv[1])
    rd = RecoverableData.ForData(
        'hello world', rs, 'A recovery test', ['bre@example.org'])

    print('%s' % rd.data)

    print('Tests passed OK. Sample: %s' % generate_recovery_code())
