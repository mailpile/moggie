from ..util.dumbcode import dumb_encode_asc, dumb_encode_bin


class BaseStorage:
    """
    Our basic Storage object: a partial Python dict with a couple of new
    introspection methods.
    """
    def __init__(self, *args, **kwargs):
        self.dict = dict(*args, **kwargs)

    def __contains__(self, *args, **kwargs):
        return self.dict.__contains__(*args, **kwargs)

    def __delitem__(self, *args, **kwargs):
        return self.dict.__delitem__(*args, **kwargs)

    def __getitem__(self, *args, **kwargs):
        return self.dict.__getitem__(*args, **kwargs)

    def __setitem__(self, *args, **kwargs):
        return self.dict.__setitem__(*args, **kwargs)

    def length(self, key):
        return len(self.dict[key])

    def get(self, *args, **kwargs):
        return self.dict.get(*args, **kwargs)

    def dump(self):
        return dumb_encode_bin({
                'dict': [(k, dumb_encode_asc(self.dict[k])) for k in self.dict]
            }, compress=1)

    def capabilities(self):
        return ['dump', 'info', 'get', 'length', 'set', 'del']

    def info(self, key=None, details=False):
        obj = self.get(key) if (key is not None) else self
        info = {'exists': obj is not None}

        if obj is not None:
            if details:
                info['type'] = str(type(obj))
            try:
                info['length'] = len(obj)
            except TypeError:
                pass

        return info
