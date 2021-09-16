import binascii
import time

import cryptography.hazmat.backends
import cryptography.hazmat.primitives.hashes
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


# These are our defaults, based on recommendations found on The Internet.
# The parameters actually used should be stored along with the output so
# we can change them later if they're found to be too weak or flawed in
# some other way.
KDF_PARAMS = {
    'pbkdf2': {
        'iterations': 400000
    },
    'scrypt': {
        'n': 2**17,
        'r': 8,
        'p': 1
    }
}


def stretch_with_pbkdf2(password, salt, params=KDF_PARAMS['pbkdf2']):
    """
    Stretch a passphrase using a salt and the pbkdf2 algorithm.

    >>> stretch_with_pbkdf2(b'hello', b'world')
    b'qntYDMyKJRmkllu6OjJUcsQ0i9gkGhzDeBfSNOSnEQs='
    """
    return binascii.b2a_base64(PBKDF2HMAC(
        backend=cryptography.hazmat.backends.default_backend(),
        algorithm=cryptography.hazmat.primitives.hashes.SHA256(),
        salt=salt,
        iterations=int(params['iterations']),
        length=32).derive(password), newline=False)


def stretch_with_scrypt(password, salt, params=KDF_PARAMS['scrypt']):
    """
    Stretch a passphrase using a salt and the scrypt algorithm.

    >>> stretch_with_scrypt(b'hello', b'world')
    b'siQFKgwjVWKV5MzgXCJSKqbxe2IMDhc8Ro5/EwKhe4Q='
    """
    return binascii.b2a_base64(Scrypt(
        backend=cryptography.hazmat.backends.default_backend(),
        salt=salt,
        n=int(params['n']),
        r=int(params['r']),
        p=int(params['p']),
        length=32).derive(password), newline=False)


class SecurePassphraseStorage(object):
    """
    This is slightly obfuscated in-memory storage of passphrases.

    The data is currently stored as an array of integers, which takes
    advantage of Python's internal shared storage for small numbers.
    This is not secure against a determined adversary, but at least the
    passphrase won't be written in the clear to core dumps or swap.

    >>> sps = SecurePassphraseStorage(passphrase='ABC')
    >>> sps.data
    [65, 66, 67]

    To copy a passphrase:

    >>> sps2 = SecurePassphraseStorage().copy(sps)
    >>> sps2.data
    [65, 66, 67]

    To check passphrases for validity, use compare():

    >>> sps.compare('CBA')
    False
    >>> sps.compare('ABC')
    True

    To extract the passphrase, use the get_reader() method to get a
    file-like object that will return the characters of the passphrase
    one byte at a time.

    >>> rdr = sps.get_reader()
    >>> rdr.seek(1)
    >>> [rdr.read(5), rdr.read(), rdr.read(), rdr.read()]
    ['B', 'C', '', '']

    If an expiration time is set, trying to access the passphrase will
    make it evaporate.

    >>> sps.expiration = time.time() - 5
    >>> sps.get_reader() is None
    True
    >>> sps.data is None
    True
    """
    # FIXME: Replace this with a memlocked ctype buffer, whenever possible

    def __init__(self, passphrase=None, stretched=False):
        self.generation = 0
        self.expiration = -1
        self.is_stretched = stretched
        self.stretch_cache = {}
        if passphrase is not None:
            self.set_passphrase(passphrase)
        else:
            self.data = None

    def copy(self, src):
        self.data = src.data
        self.expiration = src.expiration
        self.generation += 1
        return self

    def is_set(self):
        return (self.data is not None)

    def stretches(self, salt, params=None):
        if self.is_stretched:
            yield (self.is_stretched, self)
            return

        if params is None:
            params = KDF_PARAMS

        for which, name, stretch in (
                (Scrypt, 'scrypt', stretch_with_scrypt),
                (PBKDF2HMAC, 'pbkdf2', stretch_with_pbkdf2), ):
            if which:
                try:
                    how = params[name]
                    name += ' ' + json.dumps(how, sort_keys=True)
                    sc_key = '%s/%s' % (name, salt)
                    if sc_key not in self.stretch_cache:
                        pf = intlist_to_string(self.data).encode('utf-8')
                        self.stretch_cache[sc_key] = SecurePassphraseStorage(
                            stretch(pf, salt, how), stretched=name)
                    yield (name, self.stretch_cache[sc_key])
                except (KeyError, AttributeError, UnsupportedAlgorithm):
                    pass

        yield ('clear', self)

    def stretched(self, salt, params=None):
        for name, stretch in self.stretches(salt, params=params):
            return stretch

    def set_passphrase(self, passphrase):
        # This stores the passphrase as a list of integers, which is a
        # primitive in-memory obfuscation relying on how Python represents
        # small integers as globally shared objects. Better Than Nothing!
        self.data = [b for b in bytes(passphrase, 'utf-8')]
        self.stretch_cache = {}
        self.generation += 1

    def compare(self, passphrase):
        if (self.expiration > 0) and (time.time() > self.expiration):
            self.data = None
            return False
        return (self.data is not None and
                self.data == [b for b in bytes(passphrase, 'utf-8')])

    def read_byte_at(self, offset):
        if self.data is None or offset >= len(self.data):
            return ''
        return chr(self.data[offset])

    def get_passphrase(self):
        if self.data is None:
            return ''
        return intlist_to_string(self.data)

    def get_reader(self):
        class SecurePassphraseReader(object):
            def __init__(self, sps):
                self.storage = sps
                self.offset = 0

            def seek(self, offset, whence=0):
                if not (whence == 0):
                    raise ValueError('whence != 0')
                self.offset = offset

            def read(self, ignored_bytecount=None):
                one_byte = self.storage.read_byte_at(self.offset)
                self.offset += 1

                return one_byte

            def close(self):
                pass

        if (self.expiration > 0) and (time.time() > self.expiration):
            self.data = None
            return None
        elif self.data is not None:
            return SecurePassphraseReader(self)
        else:
            return None


if __name__ == "__main__":
    import doctest
    import sys
    result = doctest.testmod(optionflags=doctest.ELLIPSIS)
    print('%s' % (result, ))
    if result.failed:
        sys.exit(1)
