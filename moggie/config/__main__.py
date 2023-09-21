from . import *

if __name__ == '__main__':
    import sys
    from ..util.dumbcode import to_json
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

    # Test that the policies get inherited correctly!
    #  - paths, labels and updated times should NOT be inherited.
    #  - everything else should get inherited
    #  - tag lists get combind unless blocked by '-'
    #  - Inheritance can be blocked by setting an attribute to '-'
    #  - '-' gets converted to None and stripped from tag lists
    cz = ac.get_ephemeral_snapshot().context_zero()
    assert(
        cz.set_path('/home/bjarni', label='Bjarni', tags='bjarni', account='bre@example.org')
        is True)
    cz.set_path('/home/bjarni/99_Mail', tags='foo', watch_policy='watch', account='-')
    cz.set_path('/home/bjarni/00_Mail', tags=['-', 'inbox', 'outbox'])
    cz.set_path('imap://u@example.org', watch_policy='watch')
    # This indirectly tests partial updates in .set_path()
    cz.set_path_updated('/home/bjarni/foo', 12345)
    cz.set_path_updated('/home/bjarni', 54321)
    policies = cz.get_path_policies(
        'imap://u@example.org',
        'imap://u@example.org/INBOX',
        '/tmp',
        '/home/bjarni',
        '/home/bjarni/blargh',
        '/home/bjarni/foo',
        '/home/bjarni/00_Mail',
        '/home/bjarni/99_Mail',
        '/home/bjarni/99_Mail/sub', slim=False)
    assert(policies[b'/tmp']['label'] is None)
    assert(policies[b'/tmp']['account'] is None)
    assert(policies[b'/tmp']['watch_policy'] is None)
    assert(policies[b'/home/bjarni']['label'] == 'Bjarni')
    assert(policies[b'/home/bjarni']['updated'] == '54321')
    assert(policies[b'/home/bjarni/blargh']['label'] is None)
    assert(policies[b'/home/bjarni/blargh']['updated'] is None)
    assert(policies[b'/home/bjarni/blargh']['account'] == 'bre@example.org')
    assert(policies[b'/home/bjarni/blargh']['watch_policy'] is None)
    assert(policies[b'/home/bjarni/foo']['label'] is None)
    assert(policies[b'/home/bjarni/foo']['updated'] == '12345')
    assert(policies[b'/home/bjarni/foo']['account'] == 'bre@example.org')
    assert(policies[b'/home/bjarni/00_Mail']['tags'] == 'inbox,outbox')
    assert(policies[b'/home/bjarni/99_Mail']['tags'] == 'bjarni,foo')
    assert(policies[b'/home/bjarni/99_Mail']['label'] is None)
    assert(policies[b'/home/bjarni/99_Mail']['updated'] is None)
    assert(policies[b'/home/bjarni/99_Mail']['account'] is None)
    assert(policies[b'/home/bjarni/99_Mail']['watch_policy'] == 'watch')
    assert(policies[b'/home/bjarni/99_Mail/sub']['tags'] == 'bjarni,foo')
    assert(policies[b'/home/bjarni/99_Mail/sub']['watch_policy'] == 'watch')
    assert(policies[b'/home/bjarni/99_Mail/sub']['account'] is None)
    assert(policies[b'imap://u@example.org/INBOX']['watch_policy'] == 'watch')

    # Test deletion, make sure inherit=False gives us raw policy values
    assert(cz.set_path('/home/bjarni/99_Mail', _remove=True) is False)
    policies = cz.get_path_policies(
        '/home/bjarni/foo',
        '/home/bjarni/99_Mail',
        '/home/bjarni/00_Mail', inherit=False, slim=True)
    assert('account' not in policies[b'/home/bjarni/foo'])  # slim!
    assert(policies[b'/home/bjarni/foo']['updated'] == '12345')
    assert(policies[b'/home/bjarni/00_Mail']['tags'] == '-,inbox,outbox')
    assert('tags' not in policies[b'/home/bjarni/99_Mail'])  # Deleted

    # No paths specified = fetch all policies
    assert(len(cz.get_path_policies()) == 5)

    ac.save()
    del ac
    os.remove('/tmp/config.rc')
    print('Tests passed OK')
