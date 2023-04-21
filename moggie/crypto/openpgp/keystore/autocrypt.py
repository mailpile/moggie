import base64
import logging
import time
import sqlite3
from moggie.util import NotFoundError

from ....storage.sqlite_zip import ZipEncryptedSQLite3
from ..keystore import OpenPGPKeyStore


class AutocryptKeyStore(OpenPGPKeyStore):
    def __init__(self, **kwargs):
        self.min_count = kwargs.get('min_count')
        for kk in ('min_count',):
            if kk in kwargs:
                del kwargs[kk]

        OpenPGPKeyStore.__init__(self, **kwargs)
        self.open_db()

    COLUMNS = (
        'addr', 'last_seen', 'prefer_encrypt',
        'autocrypt_timestamp',
        'autocrypt_count',
        'public_key_fingerprint', 'public_key', 'public_key_source',
        'gossip_timestamp',
        'gossip_key_fingerprint', 'gossip_key', 'gossip_key_source')

    def open_db(self):
        if self.which in (None, ''):
            import os

            data_directory = self.resources['data_directory']
            file_namespace = self.resources['file_namespace']
            encryption_keys = self.resources.get('encryption_keys')
            ext = 'sqz' if encryption_keys else 'sq3'

            filepath = os.path.join(
                data_directory, 'autocrypt.%s.%s' % (file_namespace, ext))
            logging.info('Autocrypt DB is %s (encrypted=%s)'
                % (filepath, bool(encryption_keys)))

            self.db = ZipEncryptedSQLite3(filepath,
                encryption_keys=encryption_keys)
        else:
            self.db = ZipEncryptedSQLite3(self.which)
            logging.info('Autocrypt DB is %s (encrypted=%s)'
                % (self.which, False))

        self.key_cache = {}
        self.configure_db()

    def configure_db(self):
        self.db.execute("""CREATE TABLE IF NOT EXISTS autocrypt_peers(
                addr                   TEXT PRIMARY KEY,
                last_seen              INTEGER,
                prefer_encrypt         TEXT,
                autocrypt_timestamp    INTEGER,
                autocrypt_count        INTEGER,
                public_key_fingerprint TEXT,
                public_key             TEXT,
                public_key_source      TEXT,
                gossip_timestamp       INTEGER,
                gossip_key_fingerprint TEXT,
                gossip_key             TEXT,
                gossip_key_source      TEXT)""")

    def _cache_row(self, row):
        row_dict = dict((k, row[i]) for i, k in enumerate(self.COLUMNS))
        for col in ('addr', 'public_key_fingerprint', 'gossip_key_fingerprint'):
            if row_dict[col]:
                self.key_cache[row_dict[col]] = row_dict
        return row_dict

    def get_cert(self, fingerprint):
        self.key_cache = {}
        for which in ('public_key_fingerprint', 'gossip_key_fingerprint'):
            for row in self.db.execute("""\
                    SELECT public_key, %s
                      FROM autocrypt_peers
                     WHERE %s = ?""" % (', '.join(self.COLUMNS), which),
                    (fingerprint,)):
                self._cache_row(row[1:])
                return base64.b64decode(row[0])
        raise NotFoundError(fingerprint)

    def _select(self, what, search_terms, min_count):
        if '>' in search_terms:
            search_terms, min_count = search_terms.split('>')
        if search_terms.endswith('=mutual'):
            search_terms, mutual = search_terms.split('=')
        else:
            mutual = None

        SQL = 'SELECT %s FROM autocrypt_peers WHERE addr = ?' % what
        if mutual:
            SQL += """\
               AND prefer_encrypt = 'mutual'
               AND autocrypt_timestamp == last_seen"""
        elif min_count is None:
            min_count = self.min_count
        if min_count is not None:
            SQL += " AND autocrypt_count > %d" % int(min_count)

        return self.db.execute(SQL, (search_terms,))

    def find_certs(self, search_terms, min_count=None):
        self.key_cache = {}
        want = ', '.join(self.COLUMNS)
        for row in self._select(want, search_terms, min_count):
            row_dict = self._cache_row(row)
            if row_dict.get('public_key'):
                yield base64.b64decode(row_dict['public_key'])
            if row_dict.get('gossip_key'):
                yield base64.b64decode(row_dict['gossip_key'])

    def get_keyinfo(self, key):
        from ..keyinfo import get_keyinfo
        key_info = get_keyinfo(key)[0]
        key_info['autocrypt'] = ac = self.key_cache.get(key_info['fingerprint'])

        if ac.get('public_key') or ac.get('gossip_key'):
            d35 = 35 * 24 * 3600
            if ac['autocrypt_timestamp'] <= ac['last_seen'] - d35:
                recommendation = 'discourage'
            elif ac.get('prefer_encrypt') == 'mutual':
                recommendation = 'encrypt'
            else:
                recommendation = 'available'
        else:
            recommendation = 'unavailable'
        ac['recommendation'] = recommendation
        del ac['public_key']
        del ac['gossip_key']

        return key_info

    # list_certs is inherited, and combines get_keyinfo with find_certs.

    def process_email(self, parsed_msg, delete=True, now=None):
        """
        Returns True if we updated Autocrypt state for this user, False
        if user entry is unchanged, None if user is not in database.
        """
        from ..keyinfo import get_keyinfo

        # Ignore read-receipts and other such things
        if parsed_msg.get('content-type', [None])[0] == 'multipart/report':
            return False

        now = int(time.time()) if (now is None) else now
        effective_date = min(now, parsed_msg['_DATE_TS'])
        peer_addr = parsed_msg['from']['address']
        ac_header = parsed_msg.get('autocrypt', [None])[0]

        current = list(self.list_certs(peer_addr))
        current = current[0] if current else None
        current_ac = current['autocrypt'] if current else None
        changed = False

        # FIXME: Check for gossip headers!

        for ac_header in (parsed_msg.get('autocrypt') or []):
            try:
                for k in ac_header:
                    if (k[:1] != '_' and
                            k not in ('addr', 'prefer-encrypt', 'keydata')):
                        raise ValueError('Unknown attribute in header')
                if ac_header['addr'] != peer_addr:
                    raise ValueError('Invalid address')

                if current:
                    current_ts = current_ac['autocrypt_timestamp']
                    if effective_date <= current_ts:
                        return changed

                key_b64 = ac_header['keydata']
                key_bytes = base64.b64decode(key_b64)
                key_info = get_keyinfo(key_bytes)
                fingerprint = key_info[0]['fingerprint']

                if current:
                    self.db.execute("""\
                        UPDATE autocrypt_peers
                           SET autocrypt_count = autocrypt_count + 1,
                               autocrypt_timestamp = ?,
                               last_seen = ?,
                               public_key_fingerprint = ?,
                               public_key = ?,
                               public_key_source = ?,
                               prefer_encrypt = ?
                         WHERE addr = ?""", (
                             effective_date,
                             effective_date,
                             fingerprint,
                             key_b64,
                             parsed_msg['message-id'],
                             ac_header.get('prefer-encrypt', None),
                             peer_addr))
                else:
                    self.db.execute("""\
                        INSERT INTO autocrypt_peers(
                            addr, prefer_encrypt,
                            autocrypt_timestamp, autocrypt_count, last_seen,
                            public_key_fingerprint, public_key,
                            public_key_source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
                            peer_addr,
                            ac_header.get('prefer-encrypt', None),
                            effective_date, 1, effective_date,
                            fingerprint,
                            key_b64,
                            parsed_msg['message-id']))
                return True

            except (KeyError, ValueError):
                pass

        # If we get this far, this message has no usable Autocrypt header.
        # We still count, reset prefer_encrypt and maybe clean up.
        if not current:
            return None  # Ignore messages from unknown peers

        # If messages without Autocrypt outnumber those that had it,
        # *and* we haven't seen any Autocrypt headers from this person
        # for 180 days, purge this entry from our database.
        exp = now - 90 * 24 * 3600
        if (delete
                and current_ac['autocrypt_count'] < 1
                and current_ac['autocrypt_timestamp'] < exp):
            self.db.execute("""\
                DELETE FROM autocrypt_peers
                      WHERE addr = ? """, (peer_addr,))
            return None  # No longer in database!

        # If message is new, update our last_seen and decrement
        # autocrypt_count.
        if effective_date > current_ac['last_seen']:
            self.db.execute("""\
                UPDATE autocrypt_peers
                   SET autocrypt_count = autocrypt_count - 1,
                       last_seen = ?
                 WHERE addr = ? """, (effective_date, peer_addr))
            return True

        return changed  # Ignore old messages


if __name__ == '__main__':
    import os

    DB_FILE = '/tmp/autocrypt-test.sq3'
    TEST_KEY = """\
mDMEXEcE6RYJKwYBBAHaRw8BAQdArjWwk3FAqyiFbFBKT4TzXcVBqPTB3gmzlC/Ub7O1u
120JkFsaWNlIExvdmVsYWNlIDxhbGljZUBvcGVucGdwLmV4YW1wbGU+iJAEExYIADgCGwMFCwkIBwI
GFQoJCAsCBBYCAwECHgECF4AWIQTrhbtfozp14V6UTmPyMVUMT0fjjgUCXaWfOgAKCRDyMVUMT0fjj
ukrAPoDnHBSogOmsHOsd9qGsiZpgRnOdypvbm+QtXZqth9rvwD9HcDC0tC+PHAsO7OTh1S1TC9RiJs
vawAfCPaQZoed8gK4OARcRwTpEgorBgEEAZdVAQUBAQdAQv8GIa2rSTzgqbXCpDDYMiKRVitCsy203
x3sE9+eviIDAQgHiHgEGBYIACAWIQTrhbtfozp14V6UTmPyMVUMT0fjjgUCXEcE6QIbDAAKCRDyMVU
MT0fjjlnQAQDFHUs6TIcxrNTtEZFjUFm1M0PJ1Dng/cDW4xN80fsn0QEA22Kr7VkCjeAEC08VSTeV+
QFsmz55/lntWkwYWhmvOgE=
"""
    NOW = 1681919824
    TEST_MESSAGE = {
        '_DATE_TS': NOW - 90*24*3600,
        'message-id': '<testing>',
        'from': {'address': 'bre@klaki.net'},
        'autocrypt': [{
             'addr': 'bre@klaki.net',
             'prefer-encrypt': 'mutual',
             'keydata': TEST_KEY}]}
    RESET_MESSAGE = {
        '_DATE_TS': NOW - 10,
        'message-id': '<testing>',
        'from': {'address': 'bre@klaki.net'}}

    aks = AutocryptKeyStore(which=DB_FILE)
    assert(aks.process_email(TEST_MESSAGE, now=NOW))
    assert(not aks.process_email(TEST_MESSAGE, now=NOW))

    assert(1 == len(list(aks.find_certs('bre@klaki.net'))))
    assert(1 == len(list(aks.find_certs('bre@klaki.net=mutual'))))
    assert(1 == len(list(aks.find_certs('bre@klaki.net=mutual>0'))))
    assert(0 == len(list(aks.find_certs('bre@klaki.net>5'))))
    info, cert = list(aks.with_info(aks.find_certs('bre@klaki.net')))[0]
    assert(info['autocrypt']['recommendation'] == 'encrypt')

    # Receiving a message without an Autocrypt header will mask the
    # prefer_encrypt=mutual state, for now.
    assert(aks.process_email(RESET_MESSAGE))
    assert(0 == len(list(aks.find_certs('bre@klaki.net=mutual'))))

    info, cert = list(aks.with_info(aks.find_certs('bre@klaki.net')))[0]
    assert(info['autocrypt']['recommendation'] == 'discourage')

    print('Tests passed OK')
    aks.db.close()
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
