import time

from ..util.dumbcode import dumb_encode_bin, dumb_encode_asc
from .base import BaseStorage


class MemoryStorage(BaseStorage):
    pass 


class CacheStorage(BaseStorage):

    DEFAULT_TTL = 300

    def __init__(self, *args, **kwargs):
        BaseStorage.__init__(self, *args, **kwargs)
        self.last_expired = int(time.time()) - 10
        self.sweeps = {}
        self.expirations = {}

    def dump(self):
        self.sweep()
        return dumb_encode_bin({
                'last_expired': self.last_expired,
                'sweeps': self.sweeps,
                'expirations': self.expirations,
                'dict': [(k, dumb_encode_asc(self.dict[k])) for k in self.dict]
            }, compress=None)

    def sweep(self):
        now = int(time.time())
        for ts in range(self.last_expired, now):
            if ts in self.sweeps:
                for key in self.sweeps[ts]:
                    if self.expirations.get(key, 0) < now:
                        try:
                            del self.dict[key]
                            del self.expirations[key]
                        except KeyError:
                            pass
                del self.sweeps[ts]
            self.last_expired = ts

    def touch(self, key, ttl):
        now = time.time()
        expiration = int(now + ttl)
        self.expirations[key] = expiration
        self.sweeps[expiration] = self.sweeps.get(expiration, [])
        self.sweeps[expiration].append(key)
        if now > self.last_expired + 5:
            self.sweep()

    def __setitem__(self, key, value, ttl=DEFAULT_TTL):
        BaseStorage.__setitem__(self, key, value)
        self.touch(key, ttl)

    def __getitem__(self, key):
        if time.time() > self.last_expired:
            self.sweep()
        return BaseStorage.__getitem__(self, key)


if __name__ == "__main__":
    ms = MemoryStorage()
    print(ms.info())
