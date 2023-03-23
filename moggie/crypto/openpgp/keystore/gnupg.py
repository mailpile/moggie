# A module for accessing GnuPG keyrings

from moggie.util import NotFoundError
from moggie.util.safe_popen import ExternalProcRunner

from ..keystore import OpenPGPKeyStore


class GnuPGKeyStore(OpenPGPKeyStore, ExternalProcRunner):
    def __init__(self, binary=None, **kwargs):
        if binary is None:
            from moggie.platforms import DetectBinaries
            binary = DetectBinaries(which='GnuPG', _raise=NotFoundError)
        ExternalProcRunner.__init__(self, binary)
        OpenPGPKeyStore.__init__(self, **kwargs)

        if self.which in (None, '', 'shared'):
            self.gnupg_home_args = []
        else:
            self.gnupg_home_args = ['--homedir', self.which] 

    def get_cert(self, fingerprint):
        rc, so, se = self.run(*self.gnupg_home_args,
            '--armor', '--export', fingerprint)
        if (rc != 0) or not so.startswith(b'-----BEGIN PGP PUB'):
            raise NotFoundError(fingerprint)
        return so

    def find_certs(self, search_terms):
        for info in self.list_certs(search_terms):
            try:
                yield self.get_cert(info['fingerprint'])
            except NotFoundError:
                pass

    def list_certs(self, search_terms):
        # FIXME: Switch to --with-colons, use parser from gpgi
        rc, so, se = self.run(*self.gnupg_home_args,
            '--list-public-keys',
            '--list-options=show-only-fpr-mbox', search_terms)
        if (rc != 0) or not so.strip():
            return
        certs = {}
        for fpr, mbox in (fpr_mbox.split() for fpr_mbox in so.splitlines()):
            certs[fpr] = certs.get(fpr, {'fingerprint': fpr, 'uids': {}})
            certs[fpr]['uids'][mbox] = {}
        for fpr, info in certs.items():
            yield info

    def find_private_keys(self, search_terms, passwords={}):
        for info in self.list_private_keys(search_terms, passwords):
            try:
                yield self.get_private_key(info['fingerprint'], passwords)
            except NotFoundError:
                pass

    def _pw_and_args(self, gnupg_args, passwords):
        if passwords:
            password = list(passwords.values())[0]
            gnupg_args[:0] = [
                '--pinentry-mode=loopback',
                '--passphrase-fd=0']
            return bytes(password, 'utf-8'), gnupg_args
        return b'', gnupg_args

    def list_private_keys(self, search_terms, passwords={}):
        # FIXME: Switch to --with-colons, use parser from gpgi
        pw, gnupg_args = self._pw_and_args(self.gnupg_home_args + [
                '--list-secret-keys',
                '--list-options=show-only-fpr-mbox', search_terms],
            passwords)
        rc, so, se = self.run(*gnupg_args, input_data=pw)
        if (rc != 0) or not so.strip():
            return
        certs = {}
        for fpr, mbox in (fpr_mbox.split() for fpr_mbox in so.splitlines()):
            certs[fpr] = certs.get(fpr, {'fingerprint': fpr, 'uids': {}})
            certs[fpr]['uids'][mbox] = {}
        for fpr, info in certs.items():
            yield info

    def get_private_key(self, fingerprint, passwords={}):
        pw, gnupg_args = self._pw_and_args(self.gnupg_home_args + [
                '--armor', '--export-secret-keys', fingerprint],
            passwords)
        rc, so, se = self.run(*gnupg_args, input_data=pw)
        if (rc != 0) or not so.startswith(b'-----BEGIN PGP PRIVATE'):
            raise NotFoundError(fingerprint)
        return so


if __name__ == '__main__':
    import os
    import asyncio

    async def _al(async_iterator):
        output = []
        async for item in async_iterator:
            output.append(item)
        return output

    async def tests():
        if os.getlogin() == 'bre':
            gpg_keys = GnuPGKeyStore()

            certs = list(gpg_keys.list_certs('bjarni'))
            #print('%s' % certs)

            assert((gpg_keys.get_cert(certs[0]['fingerprint'])).startswith(b'---'))
            assert(len(list(gpg_keys.find_certs('bjarni'))) >= 4)
            assert(len(list(gpg_keys.list_certs('bjarni'))) >= 4)
   
            print('Tests passed OK')
        else:
            print('Tests passed OK (not bre, skipped GnuPG tests)')

    asyncio.run(tests())
