import copy
import io
import logging
import os
import struct
import time
import zlib

from mmap import mmap, ACCESS_WRITE

from .records import RecordStore
from ..email.metadata import Metadata
from ..util.dumbcode import dumb_decode, dumb_encode_asc, dumb_encode_bin


# This is not actually a valid Metadata entry, but it contains strings we
# expect to see, to prime the compressor/decompressor.
#
# These strings can never change once we go public, and we will run out of
# alphabet for new markers, so changes here should be only made carefully.
#
# The last entry in the table will be used to compress new data.
#
METADATA_ZDICT = b"""\
[01234,56789,[[0,"BL2hvbWUvdmFybWFpY3VybWJ4",0123456789],[]],"\
\nFrom: Facebook Twitter Google Microsoft Apple <noreply@gmail.com>\
\nTo: pay bank <anna@live.net.org.co.uk>\
\nSubject: =?UTF-8?B? =?UTF-8?Q? =?utf-8?B? =?utf-8?Q? pay here this from \
You you and for share a change new New is on post update invoice free request \
\nCc:\nDate: Mon, Tue, Wed, Fri, Sun, Sat, Sun, 1 2 3 4 5 6 7 8 9 10 11 12 13 \
14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 Jan 20 Feb 20 Mar 20 \
Apr 20 May 20 Jun 20 Jul 20 Aug 20 Sep 20 Oct 20 Nov 20 Des 20 \
+0000 -0000 +0800 +0200 -0700 (CST) (PDT) (BST) (EST) (UTC) (IST)\
\nMessage-Id:",\
{"tags": ["inbox", "outbox", "sent", "spam", "trash", "read"],\
"tags": ["inbox", "outbox", "sent", "spam", "trash", "read"],\
"tags": ["inbox", "outbox", "sent", "spam", "trash", "read"],\
{"attachments":{},"thread":[]}]"""


class IntColumn:
    def __init__(self, filepath, baseline=0, minsize=10240):
        self.filepath = filepath
        self.baseline = baseline
        self.minsize = minsize
        self.zero = struct.pack('I', 0)
        self.int_size = len(self.zero)

        if not os.path.exists(filepath):
            with open(filepath, 'wb') as fd:
                fd.write(self.zero * self.minsize)

        with open(self.filepath, 'rb+') as fd:
            self.ranking = mmap(fd.fileno(), 0, access=ACCESS_WRITE)

    def close(self):
        self.ranking.close()

    def flush(self):
        self.ranking.close()
        with open(self.filepath, 'rb+') as fd:
            self.ranking = mmap(fd.fileno(), 0, access=ACCESS_WRITE)

    def __contains__(self, idx):
        if not isinstance(idx, int):
            return False
        try:
            return (self[idx] != 0)
        except (IndexError, KeyError):
            return False

    def values(self):
        _fmt = 'I' * (len(self.ranking) // self.int_size)
        return struct.unpack(_fmt, self.ranking)

    def __iter__(self):
        return (i for (i, v) in enumerate(self.values()) if v > 0)

    def items(self, grep=None):
        if grep is None:
            return ((i, v) for (i, v) in enumerate(self.values()) if v > 0)
        else:
            return ((i, v) for (i, v) in enumerate(self.values()) if grep(v))

    def keys(self):
        return iter(self)

    def __delitem__(self, idx):
        beg = idx * self.int_size
        end = beg + self.int_size
        if (0 <= end < len(self.ranking)):
            self.ranking[beg:end] = self.zero

    def __setitem__(self, idx, value):
        value = max(self.baseline + 1, value)
        beg = idx * self.int_size
        end = beg + self.int_size
        while end > len(self.ranking):
            self.ranking.close()
            with open(self.filepath, 'rb+') as fd:
                fd.seek(0, io.SEEK_END)
                fd.write(self.zero * self.minsize)
                self.ranking = mmap(fd.fileno(), 0, access=ACCESS_WRITE)
        self.ranking[beg:end] = struct.pack('I', value - self.baseline)

    def __getitem__(self, idx):
        beg = idx * self.int_size
        end = beg + self.int_size
        if not (0 < end <= len(self.ranking)):
            raise IndexError(end)
        val = struct.unpack('I', self.ranking[beg:end])[0]
        if val == 0:
            raise KeyError()
        return self.baseline + val


def _make_cfuncs(zdict):
    comp = zlib.compressobj(level=9, zdict=(zdict*10))
    deco = zlib.decompressobj(zdict=(zdict*10))

    def compress_func(data):
        cobj = comp.copy()
        return cobj.compress(data) + cobj.flush()

    def decompress_func(data):
        cobj = deco.copy()
        return cobj.decompress(data) + cobj.flush()

    return compress_func, decompress_func


class MetadataStore(RecordStore):

    # Dividing by 16 lets us not care about 32-bit timestamp rollover
    TS_RESOLUTION = 16

    def __init__(self, workdir, store_id, aes_keys):
        super().__init__(workdir, store_id,
            sparse=True,
            compress=64,
            aes_keys=aes_keys,
            est_rec_size=400,
            target_file_size=64*1024*1024)

        self.rank_by_date = IntColumn(os.path.join(workdir, 'timestamps'))
        self.thread_ids = IntColumn(os.path.join(workdir, 'threads'))
        self.mtimes = IntColumn(os.path.join(workdir, 'mtimes'))
        self.thread_cache = None

        if 0 not in self:
            record_0 = Metadata.ghost('<internal-ghost-zero@moggie>')
            record_0.more['compress_dict'] = dumb_encode_asc(METADATA_ZDICT)
            self.set(0, record_0, encrypt=False)

        import sys
        zdict = dumb_decode(self[0].more['compress_dict'])
        compress_func, decompress_func = _make_cfuncs(zdict)
        self.encoding_kwargs = lambda: {'comp_bin': (b'm', compress_func)}
        self.decoding_kwargs = lambda: {'decomp_bin': [('m', b'm', decompress_func)]}

    def delete_everything(self, *args):
        super().delete_everything(*args)
        for f in ('timestamps', 'threads', 'mtimes'):
            if os.path.exists(os.path.join(self.workdir, f)):
                 os.remove(os.path.join(self.workdir, f))

    def flush(self):
        super().flush()
        self.rank_by_date.flush()
        self.thread_ids.flush()
        self.mtimes.flush()

    def close(self):
        super().close()
        self.rank_by_date.close()
        self.thread_ids.close()
        self.mtimes.close()

    def _get_parent_id(self, idx, metadata):
        in_reply_to = metadata.get_raw_header('In-Reply-To')
        if not in_reply_to:
            return idx

        in_reply_to = in_reply_to.split(';')[0]
        try:
            return self.key_to_index(in_reply_to)
        except KeyError:
            return self.append(
                Metadata.ghost(in_reply_to, {'missing': True}),
                keys=[in_reply_to])

    def _add_to_thread(self, idx, metadata):
        tid, root = idx, metadata

        # Find root of thread...
        thread = []
        while root.parent_id != tid:
            if tid != idx:
                thread.append(tid)
            tid = root.parent_id
            root = self[tid]
        metadata.thread_id = tid

        # Are we done?
        if tid == idx:
            return False

        # Nope, housekeeping is needed.
        kids = metadata.more.get('thread', [])
        if 'thread' in metadata:
            del metadata.more['thread']

        root.more['thread'] = sorted(list(set(
            root.more.get('thread', []) + [idx] + thread + kids)))
        self.set(tid, root, rerank=False)

        for kid in set(kids + thread):
            try:
                km = self[kid]
                if 'thread' in km.more:
                    del km.more['thread']
                self.set(kid, km, rerank=False)
                self.thread_ids[kid] = self.thread_id = tid
            except KeyError:
                pass

        return True

# The strategy here, is to record for each message which thread it belongs to;
# thread IDs are simply the index of the first message in the thread.
#
# Collapsing threads in search results can be efficiently done using the following
# algorithm:
#    1. Sort by (thread-id, date)
#    2. Deduplicate/group by thread-id
#    3. Sort by date
#
        return idx

    def _rank(self, idx, metadata, mtime=True, threading=True):
        if idx <= 0:
            return

        changed = False
        if mtime:
            metadata.mtime = mtime = int(time.time())
            self.mtimes[idx] = (metadata.mtime // self.TS_RESOLUTION)
            self.rank_by_date[idx] = max(
                1, min(mtime, int(metadata.timestamp) // self.TS_RESOLUTION))
            changed = True

        if threading:
            if metadata[metadata.OFS_PARENT_ID] in (None, 0, idx):
                metadata.parent_id = self._get_parent_id(idx, metadata)
                changed = True
            if metadata[metadata.OFS_THREAD_ID] in (None, 0, idx):
                changed = self._add_to_thread(idx, metadata) or changed

            self.thread_ids[idx] = metadata.thread_id
            self.thread_cache = None

        return changed

    def _msg_keys(self, metadata, imap_keys=False, fs_path_keys=False):
        msgid = metadata.get_raw_header('Message-Id')
        if msgid is not None:
            yield msgid
        if imap_keys or fs_path_keys:
            for ptr in metadata.pointers:
                if imap_keys and (ptr.ptr_type == ptr.IS_IMAP):
                    yield ptr.ptr_path
                elif fs_path_keys and (ptr.ptr_type == ptr.IS_FS):
                    yield ptr.ptr_path

    def _get_from_metadata(self, metadata, extra_keys, **key_kwargs):
        keys = list(self._msg_keys(metadata, **key_kwargs)) + extra_keys
        for key in keys:
            om = self.get(key)
            if om is not None:
                return om, key, keys
        return None, None, keys

    def set(self, keys, metadata, **kwargs):
        if not isinstance(metadata, Metadata):
            raise ValueError('Need instance of Metadata')
        try:
            keys = keys if isinstance(keys, list) else [keys]
            idx = self.key_to_index(keys[0])
        except KeyError:
            return self.append(metadata, keys=keys, **kwargs)

        rerank = True
        if 'rerank' in kwargs:
            rerank = kwargs.get('rerank')
            del kwargs['rerank']
        if rerank:
            self._rank(idx, metadata)
        new_keys = [idx] + [k for k in keys if not isinstance(k, int)]
        return super().set(new_keys, metadata, **kwargs)

    def update_or_add(self, metadata,
            extra_keys=[], imap_keys=False, fs_path_keys=False):
        """
        This will add metadata to the index, or if metadata is already
        present, update it with new values. Old key/value pairs and old
        pointers are preserved on update, other values get overwritten.

        Returns a tuple of (new, metadata_index).
        """
        om, msg_key, all_keys = self._get_from_metadata(metadata, extra_keys,
            imap_keys=imap_keys, fs_path_keys=fs_path_keys)
        if om is None:
            return (True, self.append(metadata, keys=all_keys))

        metadata.add_pointers(om.pointers)
        for k, v in om.more.items():
            if k not in metadata.more:
                metadata.more[k] = v

        all_keys.sort(key=lambda k: 0 if (k == msg_key) else 1)
        return (False, self.set(all_keys, metadata))

    def add_if_new(self, metadata,
            extra_keys=[], imap_keys=False, fs_path_keys=False):
        om, msg_key, all_keys = self._get_from_metadata(metadata, extra_keys,
            imap_keys=imap_keys, fs_path_keys=fs_path_keys)
        if om is not None:
            return None
        else:
            return self.set(all_keys, metadata)

    def append(self, metadata,
            extra_keys=[], imap_keys=False, fs_path_keys=False,
            **kwargs):
        if not isinstance(metadata, Metadata):
            raise ValueError('Need instance of Metadata')
        if kwargs.get('keys') is None:
            kwargs['keys'] = (
                list(self._msg_keys(metadata,
                    imap_keys=imap_keys, fs_path_keys=fs_path_keys))
                + extra_keys) or None

        # FIXME: This almost always writes twice, because of the ranking.
        #        It would be nice if that were not the case!
        idx = super().append(metadata, **kwargs)
        if self._rank(idx, metadata):
            super().set(idx, metadata)

        return idx

    def get(self, key, **kwargs):
        try:
            idx = self.key_to_index(key)
            try:
                m = Metadata(*(super().get(idx, **kwargs)))
            except (UnicodeDecodeError, ValueError):
                logging.exception('Corrupt data at idx=%s ?' % idx)
                raise TypeError('Corrupt data?')
            if m is not None:
                m[m.OFS_IDX] = idx
                try:
                    m.mtime = self.TS_RESOLUTION * self.mtimes[idx]
                    m.thread_id = self.thread_ids[idx]
                except KeyError:
                    m.thread_id = idx
            return m
        except (KeyError, TypeError):
            return kwargs.get('default', None)

    def __getitem__(self, key, **kwargs):
        idx = self.key_to_index(key)
        m = Metadata(*(super().__getitem__(idx, **kwargs)))
        m[m.OFS_IDX] = idx
        try:
            m.mtime = self.TS_RESOLUTION * self.mtimes[idx]
            m.thread_id = self.thread_ids[idx]
        except KeyError:
            m.thread_id = idx
        return m

    def __delitem__(self, key):
        # FIXME: Fetch the item, delete all the pointers!
        super().__delitem__(key)
        idx = self.key_to_index(key)
        del self.rank_by_date[idx]
        del self.thread_ids[idx]
        del self.mtimes[idx]
        self.thread_cache = None

    def _make_thread_cache(self):
        pairs = [(t, i) for i, t in self.thread_ids.items()]
        pairs.sort()
        cur = [-1]
        self.thread_cache = {}
        for t, i in pairs:
            if t != cur[0]:
                if len(cur) > 1:
                    self.thread_cache[cur[0]] = cur
                cur = [t]
            if i != t:
                cur.append(i)
        if len(cur) > 1:
            self.thread_cache[cur[0]] = cur

    def get_thread_idxs(self, thread_id):
        if self.thread_cache is None:
            self._make_thread_cache()
        return self.thread_cache.get(thread_id, [thread_id])

    def date_sorting_keyfunc(self, key):
        """
        For use with [].sort(key=...)
        """
        idx = 0
        try:
            idx = self.key_to_index(key)
            return (self.rank_by_date[idx], idx)
        except (IndexError, KeyError):
            return (0, idx)

    def thread_sorting_keyfunc(self, key):
        """
        For use with [].sort(key=...)
        """
        idx = 0
        try:
            idx = self.key_to_index(key)
            return (self.thread_ids[idx], self.rank_by_date[idx], idx)
        except (IndexError, KeyError):
            try:
                return (idx, self.rank_by_date[idx], idx)
            except (IndexError, KeyError):
                return (idx, idx, idx)


if __name__ == '__main__':
    import random, sys, os
    from ..util.dumbcode import dumb_decode

    os.system('rm -rf /home/bre/tmp/metadata-test')

    from .files import FileStorage
    fs = FileStorage(relative_to=b'/home/bre')
    ms = MetadataStore('/home/bre/tmp/metadata-test', 'metadata-test', [b'123456789abcdef0'])
    t0 = time.time()
    tcount = count = 0
    stop = 4000 if len(sys.argv) < 2 else int(sys.argv[1])
    for dn in fs.info(b'/home/bre/Mail', details=True)['contents']:
      if tcount > stop:
        break
      for fn in fs.info(dn, details=True).get('contents', []):
        count = 0
        try:
          for msg in fs.iter_mailbox(fn):
            ms.update_or_add(msg)
            count += 1
        except OSError:
          pass
        if count:
          tcount += count
          t1 = time.time()
          print(' * Added %d / %d messages to index in %.2fs (%d/s), %s'
              % (count, tcount, t1-t0, tcount / (t1-t0), dumb_decode(fn)))
          count = 0
          if tcount > stop:
            break

    while True:
        t0 = time.time()
        which = ms[random.randint(0, len(ms))]
        print(' * %d: %s' % (which.idx, which.get_raw_header('Subject')))
        print('   * thread_id=%d mtime=%d siblings=%s'
            % (which.thread_id, which.mtime, ms[which.thread_id].more.get('thread')))
        t1 = time.time()
        for i in ms.get_thread_idxs(which.thread_id):
            print('%d/%d: %s' % (i, which.thread_id, ms[i].get_raw_header('Subject')))
        t2 = time.time()
        print('   * Navigated thread in %.4fs (%.4f, %.4f)' % (t2-t0, t1-t0, t2-t1))
        if which.thread_id != which.idx:
            break

    ms = MetadataStore('/tmp/metadata-test', 'metadata-test', [b'123456789abcdef0'])
    ms.delete_everything(True, False, True)
    ms = MetadataStore('/tmp/metadata-test', 'metadata-test', [b'123456789abcdef0'])
    assert(os.path.exists('/tmp/metadata-test/timestamps'))
    try:
        ms['hello'] = 'world'
        assert(not 'reached')
    except ValueError:
        pass
    headers = b"""\
Date: Wed, 1 Sep 2021 00:03:01 GMT
Message-Id: <202109010003.181031O6020234@example.org>
In-Reply-To: <202109010003.181031O6020231@example.org>
From: root@example.org (Cron Daemon)
To: bre@example.org
Subject: Sure, sure
"""
    now = int(time.time())
    foo_ptr = Metadata.PTR(0, b'/tmp/foo', 0)
    n,i1 = ms.update_or_add(
        Metadata(now, 0, foo_ptr, headers, 0, 0, {'thing': 'stuff', 'a': 'b'}))
    n,i2 = ms.update_or_add(
        Metadata(now, 0, foo_ptr, headers, 0, 0, {'wink': 123, 'a': 'c'}),
        fs_path_keys=True)
    ms.append(Metadata(now, 0, foo_ptr, b'From: bre@klai.net'))
    ms.append(Metadata(now, 0, foo_ptr, b'From: bre@klai.net'))
    ms[100000] = Metadata(now, 0, foo_ptr, b'From: bre@klai.net')
    t1M = now + 100
    ms[1000000] = Metadata(t1M, 0, foo_ptr, b'From: bre@klai.net')

    # Ensure paths and message IDs are recorded in metadata
    assert(foo_ptr.ptr_path in ms)
    assert('<202109010003.181031O6020234@example.org>' in ms)

    assert(i1 == i2)
    assert(ms[i1].more['thing'] == 'stuff')
    assert(ms[i1].more['wink'] == 123)
    assert(ms[i1].more['a'] == 'c')
    assert('<202109010003.181031O6020234@example.org>' in ms)
    assert('<202109010003.181031O6020234@example.com>' not in ms)
    assert(ms.rank_by_date[1000000] == (t1M // MetadataStore.TS_RESOLUTION))
    assert(len(list(ms.rank_by_date.keys())) == 6)  # Including ghost for i1

    times = set([t1M //  MetadataStore.TS_RESOLUTION])
    assert(len(list(ms.rank_by_date.items(grep=times.__contains__))) == 1)

    del ms[100000]
    try:
        print('Should not exist: %s' % ms[100000])
        assert(not 'reached')
    except (KeyError, IndexError):
        pass

    try:
        print('Should not exist: %s' % ms.rank_by_date[100000])
        assert(not 'reached')
    except (KeyError, IndexError):
        pass

    print('Tests passed OK')
    #time.sleep(30)
    ms.delete_everything(True, False, True)
