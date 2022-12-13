import hashlib
import base64
import binascii
from urllib.parse import quote
from urllib.parse import unquote as tag_unquote


def tag_quote(txt):
    return (quote(txt.lower(), safe='@:').lower()
        .replace('-', '%2d')
        .replace('.', '%2e'))


def b64c(b):
    """
    Rewrite a base64 string:
        - Remove LF and = characters
        - Replace slashes by underscores

    >>> b64c("abc123456def")
    'abc123456def'
    >>> b64c("\\na/=b=c/")
    'a_bc_'
    >>> b64c("a+b+c+123+")
    'a+b+c+123+'
    """
    b = b if isinstance(b, str) else str(b, 'latin-1')
    return (b.replace('/', '_')
             .replace('=', '')
             .replace('\r', '')
             .replace('\n', ''))


def c64b(b):
    """
    Reverse the mangling done by b64c

    >>> c64b("a_bc_")
    'a/bc/==='
    """
    b = b if isinstance(b, str) else str(b, 'latin-1')
    if len(b) % 4:
        b += '=' * (4 - len(b) % 4)
    return b.replace('_', '/')


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
    b'NhX4DJ0pPtdAJof5SyLVjlKbjMeRb4+sf933+9WvTPd309eVp6AKFr9+fz+5Vh7p'
    >>> sha512b64("Hello")[:64]
    b'NhX4DJ0pPtdAJof5SyLVjlKbjMeRb4+sf933+9WvTPd309eVp6AKFr9+fz+5Vh7p'

    Keyword arguments:
    s -- The string to hash
    """
    return binascii.b2a_base64(
        _hash(hashlib.sha512, data).digest(),
        newline=False)


def sha1b64(*data):
    """
    Apply the SHA1 hash algorithm to a string
    and return the base64-encoded hash value

    >>> sha1b64("Hello")
    b'9/+ei3uy4Jtwk1pdeF4MxdnQq/A='

    >>> sha1b64(u"Hello")
    b'9/+ei3uy4Jtwk1pdeF4MxdnQq/A='

    Keyword arguments:
    s -- The string to hash
    """
    return binascii.b2a_base64(
        _hash(hashlib.sha1, data).digest(),
        newline=False)


def msg_id_hash(msg_id):
    """
    Generate a hash of the message-ID, which is compatible with
    the hashes used internally in Mailpile v1.

    >>> msg_id_hash(b'bjarni@mailpile')
    '74ef13184d5d30cf573cfa1a71ddf91092066d74'

    >>> msg_id_hash('<bjarni@mailpile>')
    '74ef13184d5d30cf573cfa1a71ddf91092066d74'

    >>> msg_id_hash('dO8TGE1dMM9XPPoacd35EJIGbXQ')
    '74ef13184d5d30cf573cfa1a71ddf91092066d74'
    """
    msg_id = msg_id if isinstance(msg_id, str) else str(msg_id, 'utf-8')
    # i5+Q3pEFTNwjo7RfOVZ5DHVaABE
    # dO8TGE1dMM9XPPoacd35EJIGbXQ
    if len(msg_id) == 27 and ('@' not in msg_id):
        msg_id = base64.b64decode(c64b(msg_id))
        return str(binascii.hexlify(msg_id), 'utf-8')

    new_msg_id = '<%s>' % msg_id.split('<', 1)[-1].split('>')[0]
    if len(new_msg_id) > 2:
        msg_id = new_msg_id
    return hashlib.sha1(bytes(msg_id, 'utf-8')).hexdigest()


# If 'python util.py' is executed, start the doctest unittest
if __name__ == "__main__":
    import doctest
    import sys
    result = doctest.testmod()
    print('%s' % (result, ))
    if result.failed:
        sys.exit(1)
