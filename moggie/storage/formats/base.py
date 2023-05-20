import mmap

from . import tag_path


class FormatBytes:
    NAME = 'bytes'
    TAG = b'b'
    FMT = b'@%x+%x'

    CHUNK_BYTES = 128*1024

    @classmethod
    def Magic(cls, parent, key, is_dir=None):
        return False  # Bytes are boring - was: (not is_dir)

    def __init__(self, parent, path, container):
        if hasattr(container, 'fileno'):
            fno = container.fileno()
            container = mmap.mmap(fno, 0, access=mmap.ACCESS_WRITE)
        self.container = container
        self.parent = parent
        self.path = path

    def _range_to_key(self, beg, end):
        return self.FMT % (beg, end-beg)

    def _key_to_range(self, key):
        if isinstance(key, bytes):
            beg, end = key[1:].split(b'+')
        else:
            beg, end = key[1:].split('+')
        beg, end = (int(beg, 16), int(end, 16))
        end += beg
        if (beg <= end <= len(self.container)):
            return beg, end
        raise IndexError('Invalid beg=%d, end=%d, max=%d'
            % (beg, end, len(self.container)))         

    def __contains__(self, key):
        try:
            b,e = self._key_to_range(key)
            return True
        except (IndexError, ValueError):
            return False

    def __getitem__(self, key):
        b,e = self._key_to_range(key)
        return self.container[b:e]

    def __delitem__(self, key):
        b,e = self._key_to_range(key)
        if hasattr(self.container, 'resize'):
            moving = len(self.container) - e
            target = b
            for chunk in range(e, len(self.container), self.CHUNK_BYTES):
                data = self.container[chunk:chunk+self.CHUNK_BYTES]
                self.container[target:target+len(data)] = data
                target += len(data)
            self.container.resize(len(self.container)-(e-b))
        else:
            # Hope the container allows slicing, otherwise we asplode.
            self.container[b:e] = []

    def __iadd__(self, data):
        self.append(data)
        return self

    def append(self, data):
        if isinstance(data, str):
            data = bytes(data, 'utf-8')
        eoc = len(self.container)
        if hasattr(self.container, 'resize'):
            self.container.resize(eoc + len(data))
        self.container[eoc:eoc+len(data)] = data
        return self.get_tagged_path(self._range_to_key(eoc, eoc+len(data)))

    def get(self, key, default=None, **kwargs):
        try:
            return self[key]
        except (IndexError, ValueError):
            return default

    def get_tagged_path(self, key):
        path = self.path + [(self.TAG, key)]
        return tag_path(*path)

    def __setitem__(self, key, value):
        b,e = self._key_to_range(key)
        if isinstance(value, str):
            value = bytes(value, 'utf-8')
        if len(value) != (e-b):
            raise ValueError('Lengths must match')
        self.container[b:e] = value

    def __iter__(self):
        return self.keys()

    def keys(self):
        return []

    def __iter__(self):
        return self.keys()

    def __len__(self):
        return sum(1 for k in self.keys())


if __name__ == "__main__":

    bc = FormatBytes([b'/tmp/fake.txt'], bytearray(b'1234567890'))
    assert('@0+a' in bc)
    assert(bc['@0+5'] == b'12345')
    bc['@0+2'] = b'xx'
    assert(bytes(bc['@0+3']) == b'xx3')
    assert('@1+2' in bc)
    del bc['@1+2']
    assert('@0+a' not in bc)
    assert('@0+8' in bc)

    assert(bc.get_tagged_path(b'@0+5') == b'/tmp/fake.txt@0+5[b:4]')

    print('Tests passed OK')
