# Store and retreive OpenPGP key material

from ....util import NotFoundError
from ..keyinfo import get_keyinfo

from .registry import KEYSTORE_REGISTRY
from .registry import DEFAULT_LOCAL_KEYSTORES, DEFAULT_KEYSTORES


class OpenPGPKeyStore:
    ALL = '*'

    def __init__(self, which=None, read_only=False, **kwargs):
        self.which = which
        self.resources = kwargs or {}
        if read_only:
            self.set_read_only()

    def set_read_only(self):
        """
        Cause the `save_*` and `delete_*` methods to raise
        PermissionError instead of attempting to modify the keystore.
        """
        setattr(self, 'save_cert', self._read_only_error)
        setattr(self, 'save_private_key', self._read_only_error)
        setattr(self, 'delete_cert', self._read_only_error)
        setattr(self, 'delete_private_key', self._read_only_error)

    def get_cert(self, fingerprint):
        """
        Attempt to retrieve the OpenPGP certificate (public key) with
        the given fingerprint from this key store. This function should
        return the ASCII armored certificate as bytes.

        It will raise NotFoundError if no matching key is found.
        """
        raise NotFoundError('get_cert not implemented by %s' % self)

    def find_certs(self, search_terms):
        """
        Retrieve all stored OpenPGP certificates (public keys) that
        match the search terms. Different key stores may search based on
        different criteria, but generally at least the UserIDs and
        fingerprints should be considered for simple substring matches.

        This function returns an iterable (a generator or list-like
        object) of 0 or more ASCII armored certificates, as bytes.

        Key stores which do not implement key searches will raise
        NotImplementedError.
        """
        raise NotImplementedError('find_certs not implemented by %s' % self)
        yield 'not-reached'

    def get_keyinfo(self, key):
        return get_keyinfo(key)

    def with_info(self, iterator):
        """
        Accepts an iterator which emits keys, generating tuples of
        (keyinfo, key).
        """
        for key in iterator:
            yield (self.get_keyinfo(key), key)

    def list_certs(self, search_terms):
        """
        Emits a list of keyinfo objects for certificates (public keys)
        matching the search terms.

        If not implemented by a subclass, the default implementation uses
        `find_certs` and `with_info` to generate output.
        """
        for info, _ in self.with_info(self.find_certs(search_terms)):
            yield info

    def get_private_key(self, fingerprint, passwords={}):
        """
        Attempt to retrieve the OpenPGP private key with the given
        fingerprint from this key store. This function should return the
        ASCII armored certificate as bytes.

        It will raise NotFoundError if no matching key is found.
        """
        raise NotFoundError('get_private_key not implemented by %s' % self)

    def find_private_keys(self, search_terms, passwords={}):
        """
        Retrieve all stored OpenPGP private keys that match the search
        terms. Different key stores may search based on different
        criteria, but generally at least the UserIDs and fingerprints
        should be considered for simple substring matches.

        This function returns an iterable (a generator or list-like
        object) of 0 or more ASCII armored private keys, as bytes.

        Key stores which do not implement key searches will raise
        NotImplementedError.
        """
        raise NotImplementedError(
            'find_private_keys not implemented by %s' % self)
        yield 'not-reached'

    def list_private_keys(self, search_terms, passwords={}):
        """
        Emits a list of keyinfo objects for private keys matching the
        search terms.

        If not implemented by a subclass, the default implementation uses
        `find_private_keys` and `with_info` to generate output.
        """
        for info, _ in self.with_info(
                self.find_private_keys(search_terms, passwords)):
            yield info

    def save_cert(self, cert):
        """
        Add a certficate to the store.

        Returns True on success, False otherwise.
        """
        raise NotImplementedError('save_cert not implemented by %s' % self)

    def save_private_key(self, private_key):
        """
        Add a private key to the store.

        Returns True on success, False otherwise.
        """
        raise NotImplementedError(
            'save_private_key not implemented by %s' % self)

    def delete_cert(self, fingerprint):
        """
        Delete the certificate with the given fingerprint from the store.

        Returns True on success.
        Raises NotFoundError if the key is not in the store.
        """
        raise NotImplementedError('delete_cert not implemented by %s' % self)

    def delete_private_key(self, fingerprint):
        """
        Delete the private key with the given fingerprint from the store.

        Returns True on success.
        Raises NotFoundError if the key is not in the store.
        """
        raise NotImplementedError(
            'delete_private_key not implemented by %s' % self)

    def _read_only_error(self, *args, **kwargs):
        raise PermissionError('%s is read-only' % self)

    def process_email(self, parsed_msg):
        raise NotImplementedError(
            'process_email not implemented by %s' % self)


class PrioritizedKeyStores(OpenPGPKeyStore):
    """
    This class implements the OpenPGPKeyStore API, but delegates all
    operations to an in-order-of-priority list of other key stores.

    All `get_`, `find_` and `list_` methods take an additioal optional
    keyword argument, `deadline`, which can be a timestamp after which
    to abort processing.

    The `find_` and `list_` methods also take an optional keyword
    argument `max_results` to cap the output volume.

    NotImpementedErrors are silently ignored in most cases, so not all
    keystores have to impement all methods.
    """
    def __init__(self, config_line, progress_callback=None, **kwargs):
        super().__init__(**kwargs)
        self.progress_cb = progress_callback or (lambda msg: None)
        self.keystores = []
        for name in (w.strip() for w in config_line.strip().split(',')):
            if ':' in name:
                k, which = name.split(':', 1)
            else:
                k, which = name, None
            cls = KEYSTORE_REGISTRY[k.lower()]
            obj = cls(which=which, **self.resources)
            self.add_keystore(name, obj)

    def get_keystore(self, name):
        for n, obj in self.keystores:
            if n == name:
                return obj
        return None

    def add_keystore(self, name, obj, first=False):
        """
        Add a keystore. By default it is added as a last resort, with
        the lowest priority. Set `first=True` to make this the preferred
        keystore.
        """
        if first:
            self.keystores[:0] = [(name, obj)]
        else:
            self.keystores.append((name, obj))

    def _choose(self, which):
        for name, store in self.keystores:
            if not which or (name.lower() == which):
                self.progress_cb(name)
                return store
        raise NotFoundError('Unknown keystore: %s' % which)

    def _do(self, method, fingerprint, deadline, _all):
        count, tried = 0, []
        for name, store in self.keystores:
            if deadline and time.time() > deadline:
                break
            try:
                self.progress_cb(name)
                rv = getattr(store, method)(fingerprint)
                if not _all:
                    return rv
                count += 1
            except NotFoundError as e:
                tried.append(name)
            except PermissionError:
                if not _all:
                    raise
            except NotImplementedError:
                pass
        if _all:
            return count
        else:
            raise NotFoundError('Tried keystores: %s' % ', '.join(tried))

    def _get(self, method, fingerprint, deadline):
        return self._do(method, fingerprint, deadline, False)

    def _srch(self, method, search_terms, max_results, deadline, *args):
        yielded = 0
        for name, store in self.keystores:
            try:
                self.progress_cb(name)
                for result in getattr(store, method)(search_terms, *args):
                    yield result
                    yielded += 1
                    if max_results and yielded >= max_results:
                        break
                    if deadline and time.time() > deadline:
                        break
            except (NotImplementedError, PermissionError):
                pass

    def get_cert(self, fingerprint, deadline=None):
        return self._get('get_cert', fingerprint, deadline)

    def list_certs(self, search_terms, max_results=None, deadline=None):
        for result in self._srch(
                 'list_certs', search_terms, max_results, deadline):
             yield result

    def find_certs(self, search_terms, max_results=None, deadline=None):
        for result in self._srch(
                 'find_certs', search_terms, max_results, deadline):
             yield result

    def get_private_key(self, fingerprint, deadline=None):
        return self._get('get_private_key', fingerprint, deadline)

    def find_private_keys(self, search_terms,
            passwords={}, max_results=None, deadline=None):
        for result in self._srch('find_private_keys',
                search_terms, max_results, deadline, passwords):
            yield result

    def list_private_keys(self, search_terms,
            passwords={}, max_results=None, deadline=None):
        for result in self._srch('list_private_keys',
                search_terms, max_results, deadline, passwords):
            yield result

    def save_cert(self, cert, which=None):
        return self._choose(which).save_cert(cert)

    def save_private_key(self, private_key, which=None):
        return self._choose(which).save_private_key(private_key)

    def delete_cert(self, fingerprint, which=None):
        if which == self.ALL:
            return self._do('delete_cert', fingerprint, 0, True)
        else:
            return self._choose(which).delete_cert(fingerprint)

    def delete_private_key(self, private_key, which=None):
        if which == self.ALL:
            return self._do('delete_private_key', private_key, 0, True)
        else:
            return self._choose(which).delete_private_key(private_key)

    def process_email(self, parsed_msg, which=None):
        if which == self.ALL:
            return self._do('process_email', parsed_msg, 0, True)
        else:
            return self._choose(which).process_email(parsed_msg)

