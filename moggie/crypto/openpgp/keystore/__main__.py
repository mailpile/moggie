##[ Tests ]##################################################################

if __name__ == '__main__':
    import asyncio
    import os
    import sys
    import hashlib

    from . import *

    with_network = ('--with-network' in sys.argv)

    TEST_ID = 'mock-test-key-123412341234'
    class MockOpenPGPKeyStore(OpenPGPKeyStore):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            c = '-----BEGIN PGP PUBLIC KEY-----\n'
            p = '-----BEGIN PGP PRIVATE KEY BLOCK-----\n'
            self.c = {TEST_ID: c, self._fpr(c): c}
            self.p = {TEST_ID: p, self._fpr(p): p}
        def _fpr(self, data):
            data = bytes(data, 'utf-8') if isinstance(data, str) else data
            return hashlib.sha1(data).hexdigest()
        def get_cert(self, fpr):
            if fpr not in self.c:
                raise NotFoundError(fpr)
            return self.c[fpr]
        def get_private_key(self, fpr):
            if fpr not in self.p:
                raise NotFoundError(fpr)
            return self.p[fpr]
        def find_certs(self, search_terms):
            try:
                yield self.get_cert(search_terms)
            except NotFoundError:
                pass
        def find_private_keys(self, search_terms, passwords={}):
            try:
                yield self.get_private_key(search_terms)
            except NotFoundError:
                pass
        def with_info(self, iterator):
            for key in iterator:
                yield ({'fingerprint': self._fpr(key)}, key)
        def save_cert(self, cert):
            self.c[self._fpr(cert)] = cert
            return True
        def save_private_key(self, pkey):
            self.p[self._fpr(pkey)] = pkey
            return True
        def delete_cert(self, fpr):
            if fpr not in self.c:
                raise NotFoundError(fpr)
            del self.c[fpr]
            return True
        def delete_private_key(self, fpr):
            if fpr not in self.p:
                raise NotFoundError(fpr)
            del self.p[fpr]
            return True

    async def _al(async_iterator):
        output = []
        async for item in async_iterator:
            output.append(item)
        return output

    async def tests():

        pgpks = OpenPGPKeyStore() 
        try:
            pgpks.get_cert('FINGERPRINT')
            assert(not 'reached')
        except NotFoundError:
            pass

        try:
            for cert in pgpks.find_certs('bjarni'):
                assert(not 'reached')
        except NotImplementedError:
            pass

        progress = []
        cks = PrioritizedKeyStores(
            DEFAULT_KEYSTORES if with_network else DEFAULT_LOCAL_KEYSTORES,
            data_directory='/tmp',
            file_namespace='testing',
            encryption_keys=[b'1234'],
            progress_callback=progress.append,
            read_only=False)

        # Make sure our mock tests don't have side effects elsewhere.
        for _, store in cks.keystores:
            store.set_read_only()

        mks = MockOpenPGPKeyStore()
        cks.add_keystore('testing', mks)

        tcert = mks.get_cert(TEST_ID)
        tfpr = mks._fpr(tcert)
        assert(tcert.startswith('-----BEGIN'))
        assert((cks.get_private_key(TEST_ID)).startswith('-----BEGIN'))

        assert(1 == len(list(cks.find_certs(tfpr))))
        assert(0 == len(list(cks.find_certs('test-not-found'))))
        assert(1 <= len(list(cks.find_private_keys(TEST_ID))))
        assert(0 == len(list(cks.find_private_keys('test-not-found'))))

        try:
            cks.delete_cert(tfpr)  # Need which=cks.ALL
            assert(not 'reached')
        except PermissionError:
            pass
        assert(cks.delete_cert(tfpr, which=cks.ALL))
        assert(not (cks.delete_cert(tfpr, which=cks.ALL)))
        assert(cks.save_cert(tcert, which='testing'))
        assert(cks.delete_cert(tfpr, which=cks.ALL))

        cks.set_read_only()

        def _check(e):
            assert('GnuPG:shared' in e)
            assert('email' in e)
            assert(('WKD' in e) == with_network)
            assert(('KOO' in e) == with_network)
        for method in (cks.get_cert, cks.get_private_key):
            try:
                cert = method('no-such-cert')
                assert(not 'reached')
            except NotFoundError as e:
                _check(str(e))
            _check(progress)
            progress[:] = []

        for method in (
                cks.save_cert, cks.save_private_key,
                cks.delete_cert, cks.delete_private_key):
            try:
                progress[:] = []
                method('foobar')
                assert(not 'reached')
            except PermissionError:
                assert(not progress)

        if os.getlogin() == 'bre':
            assert(len(list(cks.find_certs('bjarni'))) >= 4)
            print('Tests passed OK')
        else:
            print('Tests passed OK (not bre, skipped GnuPG tests)')

    asyncio.run(tests())
