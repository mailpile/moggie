import hashlib
import time

from .headers import parse_parameters


SYNC_HEADER = b'X-Moggie-Sync-%s'


def _b(t):
    return t if isinstance(t, bytes) else bytes(t, 'utf-8')

def _u(t):
    return t if isinstance(t, str) else str(t, 'utf-8')


def generate_sync_id(moggie_id, src, dest):
    """
    Generate a sync-id, based on moggie's unique app ID and what we are
    synchronizing. This lets moggie differentiate between messages it put
    in a mailbox, vs. emails placed there by other external processes.

    >>> generate_sync_id('unique-id-is-long', '/path/to/bar', '/path/to/foo')
    'unique-467fd0e48b'
    """
    data = b'%s-%s-%s' % (_b(src or ''), _b(moggie_id), _b(dest or ''))
    return '%s-%s' % (_u(moggie_id)[:6], hashlib.sha1(data).hexdigest()[:10])


def generate_sync_header(sync_id, idx, ts=None):
    """
    This generates an e-mail header with synchronization info, returned
    as a tuple of byte objects: (header-name, header-value)

    >>> generate_sync_header('unique-cc6ac8a933', 0xdead, ts=0xbeef)
    (b'X-Moggie-Sync-unique-cc6ac8a933', b't=beef; i=dead')
    """
    ts = int(ts or time.time())
    return (SYNC_HEADER % _b(sync_id), b't=%x; i=%x' % (ts, idx))


def generate_sync_fn_part(sync_id, idx, ts=None):
    """
    Generate a filename fragment embedding synchronization info, returned
    as a string. The string starts and ends with '.' characters, so other
    delimiters are unnecessary in the consumer.

    >>> generate_sync_fn_part('unique-cc6ac8a933', 0xdead, ts=0xbeef)
    '.unique-cc6ac8a933.dead-beef.'
    """
    ts = int(ts or time.time())
    idx = int(idx or 0)
    if sync_id:
        return '.%s.%x-%x.' % (_u(sync_id), idx, ts)
    else:
        return '.%x-%x.' % (idx, ts)


def get_fn_sync_info(sync_id, fn):
    """
    Extract the synchronization info from a filename, converting it to
    the same format as would be found in a sync header. Returns None if
    no matching sync info is found.

    >>> path = '/path/to/message.unique-cc6ac8a933.dead-beef.stuff.eml'
    >>> get_fn_sync_info('unique-cc6ac8a933', path)
    b't=beef; i=dead'

    >>> bogus_path = '/path/to/message.bogus-cc6ac8a933.dead-beef.stuff.eml'
    >>> get_fn_sync_info('unique-cc6ac8a933', bogus_path) is None
    True
    """
    try:
        fn, sync_id = _b(fn), _b(sync_id)
        parts = fn[fn.index(sync_id):].split(b'.')
        idx, ts = parts[1].split(b'-', 1)
        return b't=%s; i=%s' % (ts, idx)
    except (ValueError, IndexError) as e:
        return None


def get_header_sync_info(sync_id, raw_header):
    """
    Extract synchronization info from a raw message header. Throws

    >>> get_header_sync_info('mog-123', b'From: foo\\r\\nX-Moggie-Sync-mog-123: sync-info\\r\\n\\r\\nstuff')
    b'sync-info'
    """
    try:
        search = b'\n%s:' % (SYNC_HEADER % _b(sync_id))
        beg = raw_header.index(search) + len(search)
        end = beg + raw_header[beg:].index(b'\n')
        return raw_header[beg:end].strip()
    except (IndexError, ValueError) as e:
        return None


def parse_sync_info(sync_info):
    """
    Convert synchronization header data into a dictionary.

    >>> parse_sync_info(b't=beef; i=dead; extra=blah')
    {'extra': 'blah', 'ts': 48879, 'idx': 57005}

    >>> parse_sync_info(b'garbage=beef; i=dead') is None
    True
    """
    try:
        params = parse_parameters('ignore; ' + _u(sync_info))[1]
        params['ts'] = int(params.pop('t'), 16)
        params['idx'] = int(params.pop('i'), 16)
        return params
    except (KeyError, ValueError, IndexError):
        return None
