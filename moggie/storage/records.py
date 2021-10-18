import binascii
import hashlib
import io
import time
import os
import re
import struct

from mmap import mmap, ACCESS_READ, ACCESS_WRITE

from ..crypto.aes_utils import make_aes_key
from ..util.dumbcode import *
from .base import BaseStorage


def salted_encoding_sha256(salt, data):
    return hashlib.sha256(salt + dumb_encode_bin(data) + salt).digest()


def encryption_id(salt, aes_key):
    # Derive a fake key to use as an ID.
    if aes_key is None:
        return b'no'
    encrypted = salted_encoding_sha256(salt, b'KEY-ID' + aes_key)
    return binascii.hexlify(encrypted)[:16]


class ConfigMismatch(ValueError):
    pass


class RecordFile:
    def __init__(self, path, file_id, chunk_records,
            compress=False,
            padding=16,
            aes_key=None,
            create=False):

        # FIXME: Make it possible to supply multiple AES keys, and use the
        #        prefix check to figure out which one works. This will make
        #        key rotation easier.

        self.file_id = file_id
        fid = (file_id.encode('utf-8') if isinstance(file_id, str) else file_id)
        self.prefix = b'RecordFile: %s, cr=%d, encrypted=%s\r\n\r\n' % (
            fid, chunk_records, encryption_id(fid, aes_key))

        self.chunk_records = chunk_records
        self.int_size = len(struct.pack('I', 0))
        self.long_size = len(struct.pack('Q', 0))
        self.header_size = (self.int_size * chunk_records)
        self.header_size += (len(self.prefix) + self.int_size + self.long_size)
        self.compress = compress
        self.padding = b' ' * padding

        # We derive our AES key from the key provided, instead of using
        # it directly. This reduces the odds of collisions (IV reuse etc.)
        # between different storage files using the same master key.
        self.aes_key = aes_key
        if aes_key is not None:
            self.aes_key = make_aes_key(self.prefix, aes_key)
        self.aes_ctr = 0

        self.path = path
        if not os.path.exists(path):
            if not create:
                raise KeyError('No such file')
            with open(path, 'wb') as fd:
              fd.write(self.prefix)
              fd.write(b'\0' * (self.header_size - len(self.prefix)))

        self.fd = open(path, 'rb+', buffering=0)
        file_prefix = self.fd.read(len(self.prefix))
        if (file_prefix != self.prefix):
            self.fd.close()
            raise ConfigMismatch('Config mismatch in %s' % path)
        self.fd.seek(0, io.SEEK_END)
        self.mmap = mmap(self.fd.fileno(), 0, access=ACCESS_WRITE)

        self.offsets = []
        self.load_offsets()

    def load_offsets(self):
        beg = len(self.prefix)
        end = beg + self.int_size * self.chunk_records
        self.offsets = list(
            struct.unpack('I' * self.chunk_records, self.mmap[beg:end]))
        beg = end
        end = beg + self.int_size
        marker = struct.unpack('I', self.mmap[beg:end])[0]
        if (marker > 0) and marker != self.fd.tell():
            raise ValueError('File (marker=%d) is corrupt, help!' % marker)

    def __getitem__(self, idx):
        ts = time.time()
        rv = self.get(idx, default=ts)
        if rv == ts:
            raise KeyError(idx)
        return rv

    def __len__(self):
        for i in reversed(range(0, self.chunk_records)):
            if self.offsets[i] > 0:
                return (i+1)
        return 0

    def __setitem__(self, idx, value):
        self.set(idx, value)

    def __contains__(self, idx):
        return (self.offsets[idx] > 0)

    def flush(self):
        self.mmap.close()
        self.fd.flush()
        self.mmap = mmap(self.fd.fileno(), 0, access=ACCESS_WRITE)

    def safe_mmap(self, end):
        if end > len(self.mmap):
            self.flush()
        return self.mmap

    def __delitem__(self, idx):
        if not (0 <= idx < self.chunk_records):
            raise IndexError('Out of bounds: %d' % idx)
        beg = idx * self.int_size + len(self.prefix)
        end = beg + self.int_size
        self.safe_mmap(end)[beg:end] = struct.pack('I', 0)
        self.offsets[idx] = 0
        # FIXME: Overwrite actual data with zeros? Probably yes.

    def make_aes_iv(self):
        # Note: The counter is mostly there to protect us from clock jumps.
        self.aes_ctr += 1
        self.aes_ctr %= 0x100000000
        t0 = time.time()
        t1 = int(t0 * 0x000000001) % 0x100000000
        t2 = int(t0 * 0x100000000) % 0x100000000
        return struct.pack('IIII', self.aes_ctr, t1, t2, self.aes_ctr)

    def length(self, idx):
        if not (0 <= idx < self.chunk_records):
            raise IndexError('Out of bounds: %d' % idx)
        beg = self.offsets[idx]
        end = beg + 2*self.int_size
        ofs, rlen = struct.unpack('II', self.safe_mmap(end)[beg:end])
        if ofs != self.offsets[idx]:
            raise IndexError('Marker does not match: %d' % idx)
        return rlen

    def get(self, idx, default=None, decode=True, aes_key=None):
        ofs = self.offsets[idx]
        if ofs < 1:
            return default
        beg = ofs + 2*self.int_size
        end = beg + self.length(idx)
        if decode:
            aes_key = aes_key if (aes_key is not None) else self.aes_key
            return dumb_decode(self.safe_mmap(end)[beg:end], aes_key=aes_key)
        else:
            return bytes(self.safe_mmap(end)[beg:end])

    def set(self, idx, value, encode=True, encrypt=True, aes_key=None):
        if not (0 <= idx < self.chunk_records):
            raise IndexError('Out of bounds: %d' % idx)

        ofs = self.offsets[idx]
        cur_len = self.length(idx) if (ofs > 0) else 0
        compress = self.compress
        if 64 < cur_len <= (compress or cur_len):
            compress = cur_len

        if encode:
            aes_key = aes_key if (aes_key is not None) else self.aes_key
            if (aes_key is not None) and encrypt:
                aes_pair = (aes_key, self.make_aes_iv())
            else:
                aes_pair = None
            encoded = dumb_encode_bin(value,
                compress=compress,
                aes_key_iv=aes_pair)
        else:
            encoded = value

        enc_len = len(encoded)
        append = (ofs < 1) or (enc_len > cur_len)

        if encode:
            if enc_len <= cur_len:
                # Pad to the previous length, to increase the odds we will be
                # able to reuse this slot later.
                padding = b' ' * (cur_len - enc_len)
            else:
                # Always waste a bit of space, to facilitate overwrites later
                padding = self.padding
        else:
            padding = b''

        encoded = padding + encoded
        enc_len = len(encoded)
        rec_len = (2*self.int_size) + enc_len
        if append:
            self.fd.seek(0, io.SEEK_END)
            ofs = self.fd.tell()

        enc_ilen = struct.pack('I', enc_len)
        enc_iofs = struct.pack('I', ofs)

        if not append:
            end = ofs + rec_len
            self.mmap[ofs:end] = (enc_iofs + enc_ilen + encoded)
        else:
            self.fd.write(enc_iofs + enc_ilen + encoded)

            # Unsafe mmap usage follows, this is just the index
            beg = idx * self.int_size + len(self.prefix)
            end = beg + self.int_size
            self.mmap[beg:end] = struct.pack('I', ofs)
            self.offsets[idx] = ofs
            # Record how long the chunk file should be; if this does not
            # match we know we died mid-operation and may be corrupt.
            beg = self.int_size * self.chunk_records + len(self.prefix)
            end = beg + self.int_size
            self.mmap[beg:end] = struct.pack('I', self.fd.tell())

    def close(self):
        self.mmap.close()
        self.mmap = None
        self.fd.close()
        self.fd = None

    def _rotate(self, src, dst):
        if os.path.exists(dst):
            os.remove(dst)
        os.rename(src, dst)

    def compacted_time(self):
        end = self.header_size
        beg = end - self.long_size
        return struct.unpack('Q', self.mmap[beg:end])[0]

    def mark_compacted(self):
        end = self.header_size
        beg = end - self.long_size
        self.mmap[beg:end] = struct.pack('Q', int(time.time()))

    def compact(self,
            new_aes_key=None, target=None, padding=None, force=False):

        tempfile = self.path + '.tmp'
        if os.path.exists(tempfile):
            os.remove(tempfile)

        if ((not force)
                and (new_aes_key is False or new_aes_key == self.aes_key)
                and (padding is None)
                and (target is None)
                and (os.path.getmtime(self.path) - self.compacted_time() < 5)):
            print('No changes, doing nothing')
            return self

        compacted = RecordFile(tempfile, self.file_id, self.chunk_records,
            compress=self.compress,
            padding=len(self.padding) if (padding is None) else padding,
            aes_key=new_aes_key,
            create=True)
        for i in range(0, self.chunk_records):
            if i in self:
                compacted[i] = self[i]
        compacted.mark_compacted()
        compacted.padding = self.padding

        if target is None:
            self.close()
            self._rotate(self.path, self.path + '.old')
            target = self.path
        self._rotate(tempfile, target)

        return compacted


class RecordStoreReadOnly:
    def __init__(self, workdir, store_id,
            salt=b'',
            compress=None,
            hashfunc=salted_encoding_sha256,
            sparse=False,
            aes_key=None,
            est_rec_size=1024,
            target_file_size=50*1024*1024):

        self.salt = salt or b'Symbolic Showmanship'
        if (aes_key and (salt == aes_key)):
            print(
                'WARNING: %s: salt and AES key are the same, cannot rekey!'
                % self)

        self.store_id = store_id
        sid = (store_id.encode('utf-8') if isinstance(store_id, str) else store_id)
        self.prefix = (b'RecordStore: %s, encrypted=%s, ers=%d, tfs=%d\r\n\r\n'
            % (sid, encryption_id(sid + self.salt, aes_key),
               est_rec_size, target_file_size))

        self.workdir = workdir
        if not os.path.exists(workdir):
            os.mkdir(workdir, 0o700)

        # Derive a new key, so we don't keep the master key sitting around.
        self.aes_key = make_aes_key(self.prefix, aes_key) if aes_key else None
        self.hashfunc = hashfunc

        self.int_size = len(struct.pack('I', 0))
        self.hash_size = len(self.hashfunc(salt, 'testing'))
        self.hash_zero = b'\0' * self.hash_size
        self.chunk_records = 1000 * (target_file_size // (1000*est_rec_size))
        self.chunks = {}
        self.compress = (est_rec_size//2) if (compress is None) else compress
        self.sparse = sparse

        self.keys_fn = os.path.join(workdir, 'keys')
        if not os.path.exists(self.keys_fn):
            with open(self.keys_fn, 'wb') as fd:
                fd.write(self.prefix)
        self.keys_fd = open(self.keys_fn, 'rb+', buffering=0)
        if self.keys_fd.read(len(self.prefix)) != self.prefix:
            self.keys_fd.close()
            raise ConfigMismatch('Config mismatch in %s' % self.keys_fn)
        self.keys = {}
        self.load_keys()
        self.loaded = self.getmtime()
        self.loaded = os.path.getmtime(self.keys_fn)
        self.keys_fd.seek(0, io.SEEK_END)
        self.next_idx = self.calculate_next_idx()

    def close(self):
        self.keys_fd.close()

    def getmtime(self):
        fns = [f for f in os.listdir(self.workdir)
            if (f[:6] in ('chunk-', 'keys')) and ('.' not in f)]
        return max(
            os.path.getmtime(os.path.join(self.workdir, fn))
            for fn in fns)

    def refresh(self):
        modified = self.getmtime()
        if modified != self.loaded:
            for chunk in self.chunks:
                self.chunks[chunk].close()
            self.chunks = {}
            self.keys = {}
            self.load_keys()
            self.next_idx = self.calculate_next_idx()
            self.loaded = modified
        return self

    def capabilities(self):
        return ['get', 'length', 'set', 'del']

    def load_keys(self):
        beg = len(self.prefix)
        rec_size = (self.hash_size + self.int_size)
        with mmap(self.keys_fd.fileno(), 0, access=ACCESS_READ) as m:
            for slot in range(0, (len(m)-len(self.prefix)) // rec_size):
                eoi = beg + self.int_size
                end = beg + rec_size
                idx = struct.unpack('I', m[beg:eoi])[0]
                key = bytes(m[eoi:end])
                self.keys[key] = (beg, idx)
                beg = end
        if self.hash_zero in self.keys:
            del self.keys[self.hash_zero]

    def __len__(self):
        return self.next_idx

    def calculate_next_idx(self):
        chunks = [int(f.split('-')[1])
                  for f in os.listdir(self.workdir)
                  if (f[:6] == 'chunk-') and ('.' not in f)]
        if chunks:
            mc = max(chunks)
            cc = mc * self.chunk_records
            return cc + len(self.get_chunk(cc + 1)[1])
        return 0

    def hash_key(self, key):
        return self.hashfunc(self.salt, key)

    def key_to_index(self, key):
        if isinstance(key, int):
            return key

        hashed_key = self.hash_key(key)
        pos_idx = self.keys.get(hashed_key)
        if pos_idx is not None:
            return pos_idx[1]

        raise KeyError('Key not found: %s' % key)

    def get_chunk(self, idx, create=False):
        chunk = (idx // self.chunk_records)
        if chunk not in self.chunks:
            chunk_fn = os.path.join(self.workdir, 'chunk-%d' % chunk)
            self.chunks[chunk] = RecordFile(chunk_fn,
                ('RecordStore(%s), chunk %d' % (self.store_id, chunk)),
                self.chunk_records,
                compress=self.compress,
                aes_key=self.aes_key,
                create=create)

        idx %= self.chunk_records
        return (idx, self.chunks[chunk])

    def __contains__(self, key):
        try:
            (idx, chunk) = self.get_chunk(self.key_to_index(key))
            return (idx in chunk)
        except KeyError:
            return False

    def length(self, key):
        (idx, chunk) = self.get_chunk(self.key_to_index(key))
        return chunk.length(idx)

    def __getitem__(self, key):
        (idx, chunk) = self.get_chunk(self.key_to_index(key))
        return chunk[idx]

    def get(self, key, decode=True, default=None, aes_key=None):
        try:
            (idx, chunk) = self.get_chunk(self.key_to_index(key))
            return chunk.get(idx, default=default, decode=decode, aes_key=aes_key)
        except KeyError:
            return default


class RecordStore(RecordStoreReadOnly):
    # FIXME: We should probably lock the file, there should only be one
    #        writer. Advisory locks are fine.
    def refresh(self):
        pass

    def flush(self):
        for c in self.chunks:
            self.chunks[c].close()
        self.chunks = {}

    def close(self):
        self.flush()
        self.keys_fd.close()

    def delete_everything(self, c1, c2, c3):
        assert(c1 and not c2 and c3)
        self.keys_fd.close()
        del self.keys
        for c in self.chunks:
            self.chunks[c].close()
        del self.chunks
        for f in os.listdir(self.workdir):
            if (f == 'keys') or f.startswith('chunk-'):
                os.remove(os.path.join(self.workdir, f))

    def __delitem__(self, key):
        (idx, chunk) = self.get_chunk(self.key_to_index(key))
        del chunk[idx]
        # FIXME: Remove all keys pointing to this data

    def del_key(self, key):
        pass  # FIXME: Remove only the key, not the data

    def set_key(self, key, idx):
        hashed_key = self.hash_key(key)
        self.keys[hashed_key] = (self.keys_fd.tell(), idx)
        output = struct.pack('I', idx) + hashed_key
        self.keys_fd.write(output)

    def __setitem__(self, key, value):
        self.set(key, value)

    def set(self, key, value, encode=True, encrypt=True, aes_key=None):
        try:
            full_idx = self.key_to_index(key)
            (c_idx, chunk) = self.get_chunk(full_idx, create=self.sparse)
            chunk.set(c_idx, value,
                encode=encode, encrypt=encrypt, aes_key=aes_key)
            if full_idx >= self.next_idx:
                self.next_idx = full_idx + 1
            return full_idx
        except KeyError:
            if isinstance(key, int):
                raise
            return self.append(value,
                keys=[key], encode=encode, encrypt=encrypt, aes_key=aes_key)

    def append(self, value, keys=None, encode=True, encrypt=True, aes_key=None):
        if (keys is not None):
            for key in (keys if isinstance(keys, list) else [keys]):
                if isinstance(key, int):
                    raise KeyError('Keys must not be ints')

        full_idx = len(self)
        (c_idx, chunk) = self.get_chunk(full_idx, create=True)
        chunk.set(c_idx, value, encode=encode, encrypt=encrypt, aes_key=aes_key)
        if full_idx >= self.next_idx:
            self.next_idx = full_idx + 1

        if keys is not None:
            for key in (keys if isinstance(keys, list) else [keys]):
                self.set_key(key, full_idx)

        return full_idx

    def compact(self, new_aes_key=False, force=False):
        aes_key = self.aes_key if (new_aes_key is False) else new_aes_key
        for idx in range(0, self.next_idx, self.chunk_records):
            (_, chunk_obj) = self.get_chunk(idx)
            chunk_obj = chunk_obj.compact(new_aes_key=aes_key, force=force)
            self.chunks[(idx // self.chunk_records)] = chunk_obj


if __name__ == "__main__":
    test_key = b'1234123412341234'

    cleaner = RecordStore('/tmp/rs-test', 'testing')
    cleaner.delete_everything(True, False, True)
    del cleaner

    rs = RecordStore('/tmp/rs-test', 'testing',
        aes_key=test_key, target_file_size=10240000)
    if os.path.exists('/tmp/rs-test/testing'):
        os.remove('/tmp/rs-test/testing')
    assert(len(rs) == 0)

    rf = RecordFile('/tmp/rs-test/testing.tmp', 'test', 128, create=True)
    assert(rf.int_size == 4)
    assert(len(rf) == 0)
    rf[0] = 'hello1'
    assert(len(rf) == 1)
    rf[1] = 'hello2'
    assert(len(rf) == 2)
    rf[2] = 43
    assert(len(rf) == 3)
    rf[0] = 'hello world'
    rf[1] = 'shrt'
    assert(rf[0] == 'hello world')
    assert(rf[1] == 'shrt')
    assert(rf[2] == 43)
    del(rf[0])
    assert(len(rf) == 3)  # Length stays the same even if sparse
    try:
        print('Should never happen: %s' % (rf[0],))
        assert(not 'reached')
    except KeyError:
        pass
    rf[0] = b'I am back again and should be at the front, oh yes'
    assert(0 in rf)
    rf = rf.compact(new_aes_key=None, padding=0, force=True)
    assert(time.time() - rf.compacted_time() < 1)

    assert(rs.hash_size == 32)
    assert(rs.chunk_records == (1000 * (10*1024*1024 // 1024000)))
    assert(len(rs.hash_key('hello')) == rs.hash_size)
    try:
        rs['hello']
        assert(not 'reached')
    except KeyError:
        pass

    assert(len(rs) == 0)
    rs['hello'] = 'world'
    assert(rs['hello'] == 'world')
    rs.refresh()
    assert(len(rs) == 1)
    rs['zeros'] = b'\0' * 10240
    assert(len(rs['zeros']) == 10240)
    assert(len(rs) == 2)
    assert(2 == rs.append('ohai'))
    assert(len(rs) == 3)
    assert(rs[2] == 'ohai')

    import random
    load = 25000
    t0 = time.time()
    for i in range(0, load):
        rs.append(i, keys=('%d' % i))
    t1 = time.time()
    for i in range(0, load):
        rs['%d' % random.randint(0, load)] = i
    t2 = time.time()
    for i in range(0, load):
        try:
            b = rs['%d' % random.randint(0, load)]
        except KeyError:
            pass
    t3 = time.time()
    for i in range(0, load):
        try:
            b = rs[random.randint(0, load)]
        except KeyError:
            pass
    t4 = time.time()

    rs2 = RecordStoreReadOnly('/tmp/rs-test', 'testing',
        aes_key=test_key, target_file_size=10240000)
    assert(rs2['hello'] == 'world')
    rs['synctest'] = 'out of sync'
    assert('synctest' not in rs2)
    rs2.refresh()
    assert(rs2['synctest'] == 'out of sync')
    assert(rs2[2] == 'ohai')
    assert(len(rs2['zeros']) == 10240)

    try:
        # Make sure that if we get the parameters wrong, we don't
        # just go reading/writing corrupt data.
        rs3 = RecordStoreReadOnly('/tmp/rs-test', 'testing',
            aes_key=test_key, target_file_size=10240001)
        assert(not 'reached')
    except ConfigMismatch:
        pass

    rs.delete_everything(True, False, True)

    print(('OK, '
          '%d appends/upd/key-reads/reads in %.2f/%.2f/%.2f/%.2f secs'
        ) % (load, t1-t0, t2-t1, t3-t2, t4-t3))
