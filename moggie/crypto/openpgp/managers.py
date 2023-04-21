import logging

class CachingKeyManager:
    def __init__(self, sop, keys):
        self.sop = sop
        self.keys = keys

        self.cert_cache = {}
        self.pkey_cache = {}

    def drop_caches(self):
        self.cert_cache = {}
        self.pkey_cache = {}
        return True

    def cached_get_pkey(self, fpr, keypasswords={}):
        if fpr not in self.pkey_cache:
            if '@' in fpr:
                # FIXME: Do we want to be more specific about the e-mail
                #        searches?
                pkeys = list(set(
                    self.keys.find_private_keys(fpr, keypasswords)))
                if len(pkeys) == 1:
                    logging.debug('Cached private key for %s' % fpr)
                    self.pkey_cache[fpr] = pkeys[0]
            else:
                self.pkey_cache[fpr] = (
                    self.keys.get_private_key(fpr, keypasswords))
        return self.pkey_cache[fpr]

    def cached_get_cert(self, fpr):
        if fpr not in self.cert_cache:
            if '@' in fpr:
                # FIXME: Do we want to be more specific about the e-mail
                #        searches?
                certs = list(set(self.keys.find_certs(fpr)))
                if len(certs) == 1:
                    logging.debug('Cached certificate for %s' % fpr)
                    self.cert_cache[fpr] = certs[0]
            else:
                self.cert_cache[fpr] = self.keys.get_cert(fpr)
        return self.cert_cache[fpr]

    def filter_key_args(self, v, _all=None):
        if isinstance(v, str):
            if v[:6] == '@PKEY:':
                return self.cached_get_pkey(v[6:], _all.get('keypasswords'))
            elif v[:6] == '@CERT:':
                return self.cached_get_cert(v[6:])
        elif isinstance(v, dict):
            for k in v:
                v[k] = self.filter_key_args(v[k], _all=(_all or v))
        elif isinstance(v, list):
            v = [self.filter_key_args(item, _all=(_all or v)) for item in v]
        elif isinstance(v, tuple):
            v = tuple(self.filter_key_args(item, _all=(_all or v)) for item in v)
        return v 
