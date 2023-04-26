from . import *

if __name__ == '__main__':
    import sys
    if os.path.exists('/tmp/config.rc'):
        os.remove('/tmp/config.rc')

    ac = AppConfig('/tmp')
    ac.provide_passphrase('Hello world, this is my passphrase')
    ac.provide_passphrase('Hello world, this is my passphrase')
    try:
        ac.provide_passphrase('Bogus')
        assert(not 'reached')
    except PermissionError:
        pass
    try:
        ac.generate_master_key()
    except PermissionError:
        pass

    ac[ac.IDENTITY_PREFIX + '1'].update({
        'name': 'Bjarni',
        'address': 'bre@example.org',
        'signature': 'Multiline\nsignature'})

    ac[ac.CONTEXT_PREFIX + '1'].update({
        'username': 'Bjarni'})

    ac[ac.ACCOUNT_PREFIX + '1'].update({
        'name': 'Bjarni',
        'addresses': 'bre@example.org'})

    ac.set_private(ac.CONTEXT_PREFIX + '1', 'password', 'very secret password')
    ac.set(ac.CONTEXT_PREFIX + '1', 'password', 'another very secret password')

    with ac:
      ac.access_zero()
      ac[ac.ACCESS_PREFIX + '1'].update({
        'name': 'Test access',
        'tokens': '12341234:0, 9999:1',
        'roles': 'Context 1:A, Context 2:r'})

      for acl in ac.all_access.values():
        #print('%s: tokens=%s, roles=%s' % (acl.name, acl.tokens, acl.roles))
        acl.roles['Context 2'] = 'aPpCcTtrwx'
        acl.tokens['abacab'] = int(time.time())

    assert(ac.access_from_token('12341234').name == 'Test access')
    try:
        ac.access_from_token('9999')
        assert(not 'reached')
    except PermissionError:
        pass

    assert(len(ac.get_aes_keys()) == 1)
    ac.change_master_key()
    old_keys = ac.get_aes_keys()
    assert(len(old_keys) == 2)

    ac.change_config_key('this is my new passphrase')

    assert(ac.get_aes_keys() == old_keys)
    assert(len(old_keys) == 2)
    assert(len(old_keys[0]) > 20)
    assert(len(old_keys[1]) > 20)

    cz = ac.context_zero()
    cz.set_secret('bjarni is silly', {'secret': 'hello world', 'magic': 1})
    cz.set_secret('bjarni is very silly', 'ohai world')
    cz.set_secret('bjarni is very silly', 'ohai world', ttl=10)
    assert(cz.get_secret('bjarni is silly')['secret'] == 'hello world')
    cz.set_secret('bjarni is silly', None)
    assert(cz.get_secret('bjarni is silly') is None)
    assert(cz.get_secret('bjarni is very silly') == 'ohai world')
    keys, sopc = cz.get_openpgp_settings()
    assert('WKD' in keys)
    assert('PGPy' in sopc)

    acct = ac.get_account('bre@example.org')
    acct.addresses.append(b'bre2@example.org')
    assert(acct.addresses[0] == 'bre@example.org')
    assert(acct.addresses[1] == 'bre2@example.org')
    assert('bre@example.org' in ac.get_account('Bjarni').addresses)
    assert('bre@example.org' in ac.get_account('Account 1').addresses)

    os.remove('/tmp/config.rc')
    print('Tests passed OK')
