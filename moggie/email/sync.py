import hashlib
import time

from .util import IDX_MAX
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
    ('unique-id-is', '467fd0e4')
    """
    data = b'%s-%s-%s' % (_b(src or ''), _b(moggie_id), _b(dest or ''))
    return (_u(moggie_id)[:12], hashlib.sha1(data).hexdigest()[:8])


def generate_sync_header(sync_id, idx, ts=None):
    """
    This generates an e-mail header with synchronization info, returned
    as a tuple of byte objects: (header-name, header-value)

    >>> sync_id = ('unique-id-is', 'cc6ac8a9')
    >>> generate_sync_header(sync_id, 0xdead, ts=0xbeef)
    (b'X-Moggie-Sync-unique-id-is', b's=cc6ac8a9; t=beef; i=dead')
    """
    hpart, vpart = sync_id
    ts = int(ts or time.time())
    return (SYNC_HEADER % _b(hpart), b's=%s; t=%x; i=%x' % (_b(vpart), ts, idx))


def generate_sync_fn_part(sync_id, idx, ts=None):
    """
    Generate a filename fragment embedding synchronization info, returned
    as a string. The string starts and ends with '.' characters, so other
    delimiters are unnecessary in the consumer.

    >>> sync_id = ('unique-id-is', 'cc6ac8a9')
    >>> generate_sync_fn_part(sync_id, 0xdead, ts=0xbeef)
    '.unique-id-is-cc6ac8a9.dead-beef.'
    """
    hpart, vpart = sync_id
    ts = int(ts or time.time())
    idx = int(idx or 0)
    if sync_id:
        return '.%s-%s.%x-%x.' % (_u(hpart), _u(vpart), idx, ts)
    else:
        return '.%x-%x.' % (idx, ts)


def get_fn_sync_info(sync_id, fn):
    """
    Extract the synchronization info from a filename, converting it to
    the same format as would be found in a sync header. Returns None if
    no matching sync info is found.

    >>> sync_id = ('unique-id-is', 'cc6ac8a9')
    >>> path = '/path/to/message.unique-id-is-cc6ac8a9.dead-beef.stuff.eml'
    >>> get_fn_sync_info(sync_id, path)
    b's=cc6ac8a9; t=beef; i=dead'

    >>> bogus_path = '/path/to/message.bogus-cc6ac8a933.dead-beef.stuff.eml'
    >>> get_fn_sync_info(sync_id, bogus_path) is None
    True
    """
    try:
        hpart, vpart = sync_id
        fn, hpart, vpart = _b(fn), _b(hpart), _b(vpart)
        parts = fn[fn.index(hpart):].split(b'.')
        idx, ts = parts[1].split(b'-', 1)
        _hp, vp = parts[0].rsplit(b'-', 1)
        return b's=%s; t=%s; i=%s' % (vp, ts, idx)
    except (ValueError, IndexError) as e:
        return None


def get_header_sync_info(sync_id, raw_header):
    """
    Extract synchronization info from a raw message header.

    >>> sync_id = ('mog', '123')
    >>> get_header_sync_info(sync_id, b'From: foo\\r\\nX-Moggie-Sync-mog: s=123; sync-info\\r\\n\\r\\nstuff')
    b's=123; sync-info'
    """
    try:
        search = b'\n%s:' % (SYNC_HEADER % _b(sync_id[0]))
        beg = raw_header.index(search) + len(search)
        end = beg + raw_header[beg:].index(b'\n')
        return raw_header[beg:end].strip()
    except (IndexError, ValueError) as e:
        return None


def parse_sync_info(sync_info, sync_id=None):
    """
    Convert synchronization header data into a dictionary.

    >>> sync_id = ('mog', '123')
    >>> parse_sync_info(b's=123; t=beef; i=dead; extra=blah', sync_id)
    {'extra': 'blah', 'ts': 48879, 'idx': 57005, 'sync': True}

    >>> parse_sync_info(b's=456; t=beef; i=dead; extra=blah', sync_id)
    {'extra': 'blah', 'ts': 48879, 'idx': 57005}

    >>> parse_sync_info(b's=456; t=beef; i=deadbeef0000; extra=blah')
    {'extra': 'blah', 'ts': 48879}

    >>> parse_sync_info(b'garbage=beef; i=dead') is None
    True
    """
    try:
        params = parse_parameters('ignore; ' + _u(sync_info))[1]
        params['ts'] = int(params.pop('t'), 16)

        params['idx'] = int(params.pop('i'), 16)
        if params['idx'] > IDX_MAX:
            params.pop('idx')

        sync_part = params.pop('s', None) 
        if sync_id and (sync_part == sync_id[1]):
            params['sync'] = True

        return params
    except (KeyError, ValueError, IndexError):
        return None
