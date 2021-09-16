import hashlib
import binascii


def _hash(cls, data):
    h = cls()
    for s in data:
        if isinstance(s, str):
            h.update(s.encode('utf-8'))
        else:
            h.update(s)
    return h


def sha512b64(*data):
    """
    Apply the SHA512 hash algorithm to a string
    and return the base64-encoded hash value

    >>> sha512b64(b"Hello")[:64]
    'NhX4DJ0pPtdAJof5SyLVjlKbjMeRb4+sf933+9WvTPd309eVp6AKFr9+fz+5Vh7p'
    >>> sha512b64("Hello")[:64]
    'NhX4DJ0pPtdAJof5SyLVjlKbjMeRb4+sf933+9WvTPd309eVp6AKFr9+fz+5Vh7p'

    Keyword arguments:
    s -- The string to hash
    """
    return binascii.b2a_base64(
        _hash(hashlib.sha512, data).digest(),
        newline=False)


