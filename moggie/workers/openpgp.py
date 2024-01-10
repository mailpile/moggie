# This is moggie's OpenPGP and Autocrypt worker.
#
#   ... since SOP is stateless, we COULD share instances between
#       contexts. But belt-and-suspenders suggests separate
#       processes for different securtiy contexts makes senase?
#   ... The keystores are 100% state.
#
#
# TODO/IDEAS/brainstorming:
#
#   - Answer questions about capabilities for a given recipient
#      - How much do we need to partition this by context? A lot?
#   - For notmuch-compatibility, we need to store session keys
#   - Use GnuPG? Or use PGPy?
#   - If using PGPy:
#      - We need our own keystore
#      - We need to keep old/expired/revoked keys around, to deal with old mail
#        ... or store session keys and summaries
#        ... or even just store decrypted content
#      - We will need to handle revocation etc. ourselves
#   - Implement the Autocrypt state machine
#      - This should be per-(tag_namespace, recipient)
#
# ... If we are partitioning by context etc, should we just use the search
# engine itself for lookups? Which implies we store keys and things inside
# emails? Wait no it doesn't. It just implies we use IDs from the same
# sequence. Or not even that. Just use same search engine code and the same
# namespaces. We have options!
#
import base64
import logging
import time
import traceback
import threading

from ..util.dumbcode import *
from ..crypto.openpgp.managers import CachingKeyManager
from ..crypto.openpgp.keystore import PrioritizedKeyStores, DEFAULT_KEYSTORES
from ..crypto.openpgp.sop import GetSOPClient, SOPError
from ..crypto.aes_utils import make_aes_key

from .base import BaseWorker


class OpenPGPWorker(BaseWorker):
    KIND = 'openpgp'

    PEEK_BYTES = 8192
    BLOCK = 8192

    def __init__(self,
            unique_app_id, status_dir, data_directory, encryption_keys,
            name=KIND,
            notify=None,
            log_level=logging.ERROR,
            shutdown_idle=False,
            keystore_config=DEFAULT_KEYSTORES,
            sop_config=None,
            metadata=None,
            tag_namespace=None,
            search=None):

        BaseWorker.__init__(self, unique_app_id, status_dir,
            name=name, notify=notify,
            log_level=log_level, shutdown_idle=shutdown_idle)

        # We derive our AES key(s) from those provided, instead of using
        # directly. This reduces the odds of collisions (IV reuse etc.)
        # between different storage files using the same master key.
        if encryption_keys:
            nbytes = bytes(name, 'utf-8')
            encryption_keys = [
                base64.b64encode(make_aes_key(nbytes, key)).strip()
                for key in encryption_keys]

        # Directly expose the KeyStore methods
        self.keystore = PrioritizedKeyStores(keystore_config,
            encryption_keys=encryption_keys,
            data_directory=data_directory,
            file_namespace=name,
            tag_namespace=tag_namespace,
            metadata=metadata,
            search=search)
        self.expose_object(self.keystore)

        # Directly expose the SOP methods, but filter the arguments to
        # implement our magic @CERT: and @PKEY: key lookup prefixes.
        self.sop = GetSOPClient(sop_config)
        self.key_cache = CachingKeyManager(self.sop, self.keystore)
        self.expose_object(self.sop,
            arg_filter=self.key_cache.filter_key_args)

        self.functions.update({
            b'drop_caches':  (True, self.api_drop_caches)})

    def drop_caches(self, remote=True):
        self.key_cache.drop_caches()
        if remote:
            return self.call('drop_caches')
        return True

    def api_drop_caches(self, **kwargs):
        return self.drop_caches(remote=False)

    def _main_httpd_loop(self):
        autocrypt = self.keystore.get_keystore('autocrypt')
        if autocrypt:
            autocrypt.open_db()
            autocrypt.db.start_background_saver()
        super()._main_httpd_loop()
        if autocrypt:
            autocrypt.db.close()


if __name__ == '__main__':
    pw = OpenPGPWorker('/tmp', None, name='moggie-test-openpgp').connect()
    if pw:
        try:
            assert(0 < len(pw.find_certs('bre@mailpile.is')['result']))

            print(', '.join(dir(pw)))

            print('** Tests passed, waiting... **')
            pw.quit()
            pw.join()
        finally:
            pw.terminate()
