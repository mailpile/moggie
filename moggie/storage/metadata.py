import io
import os
import struct
import time

from mmap import mmap, ACCESS_WRITE

from .records import RecordStore
from ..email.metadata import Metadata


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


class MetadataStore(RecordStore):

    # Dividing by 30 lets us not care about 32-bit timestamp rollover
    TS_RESOLUTION = 30

    def __init__(self, workdir, store_id, aes_key):
        super().__init__(workdir, store_id,
            sparse=True,
            compress=400,
            aes_key=aes_key,
            est_rec_size=400,
            target_file_size=64*1024*1024)

        self.thread_cache = {}

        if 0 not in self:
            self[0] = self.ghost('<internal-ghost-zero@moggie>')
        self.rank_by_date = IntColumn(os.path.join(workdir, 'timestamps'))
        self.thread_ids = IntColumn(os.path.join(workdir, 'threads'))
        self.mtimes = IntColumn(os.path.join(workdir, 'mtimes'))

    def ghost(self, msgid, more=None):
        msgid = msgid if isinstance(msgid, bytes) else bytes(msgid, 'latin-1')
        return Metadata(0, '', 0, 0, 0, b'Message-Id: %s' % msgid, more=more)

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

    def _add_to_thread(self, idx, metadata):
        in_reply_to = metadata.get_raw_header('In-Reply-To')
        if in_reply_to is not None:
            try:
                pidx = self.key_to_index(in_reply_to)
                return self.thread_ids[pidx]
            except KeyError:
                pidx = self.append(self.ghost(in_reply_to, {'missing': True}),
                    keys=[in_reply_to])
                return self.thread_ids[pidx]
#
# The strategy here, is to record for each message which thread it belongs to;
# thread IDs are simply the index of the first message in the thread.
#
# Collapsing threads in search results can be efficiently done using the following
# algorithm:
#    1. Sort by (thread-id, date)
#    2. Deduplicate by thread-id
#    3. Sort by date
#
        return idx

    def _rank(self, idx, metadata):
        if idx <= 0:
            return
        metadata.mtime = int(time.time())
        self.mtimes[idx] = (metadata.mtime // self.TS_RESOLUTION)
        self.rank_by_date[idx] = max(1,
            int(metadata.timestamp) // self.TS_RESOLUTION)
        if metadata.thread_id is None:
            metadata.thread_id = self._add_to_thread(idx, metadata)
        self.thread_ids[idx] = metadata.thread_id
        self.thread_cache = {}

    def set(self, key, metadata, **kwargs):
        if not isinstance(metadata, Metadata):
            raise ValueError('Need instance of Metadata')
        # FIXME:
        #   If we are updating an existing entry, we might be upgrading a
        #   ghost to a real message and might need to adjust thread IDs,
        #   if we ourselves have a parent.
        idx = super().set(key, metadata, **kwargs)
        self._rank(idx, metadata)
        return idx

    def update_or_add(self, metadata):
        msgid = metadata.get_raw_header('Message-Id')
        if msgid is None:
            return self.append(metadata, keys=[])
        else:
            return self.set(msgid, metadata)

    def append(self, metadata, **kwargs):
        if not isinstance(metadata, Metadata):
            raise ValueError('Need instance of Metadata')
        if kwargs.get('keys') is None:
            msgid = metadata.get_raw_header('Message-Id')
            kwargs['keys'] = [msgid] if msgid else None
        idx = super().append(metadata, **kwargs)
        self._rank(idx, metadata)
        return idx

    def get(self, key, **kwargs):
        idx = self.key_to_index(key)
        m = Metadata(*(super().get(idx, **kwargs)))
        if m is not None:
            m.idx = idx
            m.thread_id = self.thread_ids[idx]
            m.mtime = self.TS_RESOLUTION * self.mtimes[idx]
        return m

    def __getitem__(self, key, **kwargs):
        idx = self.key_to_index(key)
        m = Metadata(*(super().__getitem__(idx, **kwargs)))
        m.idx = idx
        m.thread_id = self.thread_ids[idx]
        m.mtime = self.TS_RESOLUTION * self.mtimes[idx]
        return m

    def __delitem__(self, key):
        super().__delitem__(key)
        idx = self.key_to_index(key)
        del self.rank_by_date[idx]
        del self.thread_ids[idx]
        del self.mtimes[idx]
        self.thread_cache = {}

    def get_thread_idxs(self, thread_id):
        if thread_id not in self.thread_cache:
            self.thread_cache[thread_id] = list(
                self.thread_ids.items(grep=thread_id.__eq__))
        return self.thread_cache[thread_id]

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


if __name__ == '__main__':
    import random

    ms = MetadataStore('/home/bre/tmp/metadata-test', 'metadata-test', b'123456789abcdef0')
    ms.delete_everything(True, False, True)

    from .files import FileStorage
    fs = FileStorage(relative_to=b'/home/bre')
    ms = MetadataStore('/home/bre/tmp/metadata-test', 'metadata-test', b'123456789abcdef0')
    t0 = time.time()
    tcount = count = 0
    stop = 400000
    for dn in fs.info('B/home/bre/Mail', details=True)['contents']:
      if tcount > stop:
        break
      for fn in fs.info(dn, details=True).get('contents', []):
        count = 0
        for msg in fs.info(fn, parse=True, details=True).get('emails', []):
          ms.update_or_add(msg)
          count += 1
        if count:
          tcount += count
          count = 0
          t1 = time.time()
          print('Added %d messages to index in %.2fs (%d/s), %s'
              % (tcount, t1-t0, tcount / (t1-t0), fn))
          if tcount > stop:
            break

    t0 = time.time()
    which = ms[random.randint(0, len(ms))]
    #print('%s' % which)
    print('%d: %s' % (which.idx, which.get_raw_header('Subject')))
    print('thread_id=%d mtime=%d' % (which.thread_id, which.mtime))
    t1 = time.time()
    for (i, tid) in ms.get_thread_idxs(which.thread_id):
        print('%d/%d: %s' % (i, tid, ms[i].get_raw_header('Subject')))
    t2 = time.time()
    print('Navigated thread in %.4fs (%.4f, %.4f)' % (t2-t0, t1-t0, t2-t1))

    ms = MetadataStore('/tmp/metadata-test', 'metadata-test', b'123456789abcdef0')
    ms.delete_everything(True, False, True)
    ms = MetadataStore('/tmp/metadata-test', 'metadata-test', b'123456789abcdef0')
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
    i1 = ms.update_or_add(Metadata(int(time.time()), '/tmp/foo', 0, 0, 0, headers))
    ms.append(Metadata(int(time.time()), '/tmp/foo', 0, 0, 0, b'From: bre@klai.net'))
    ms.append(Metadata(int(time.time()), '/tmp/foo', 0, 0, 0, b'From: bre@klai.net'))
    ms[100000] = Metadata(int(time.time()), '/tmp/foo', 0, 0, 0, b'From: bre@klai.net')
    t1M = int(time.time() + 100)
    ms[1000000] = Metadata(t1M, '/tmp/foo', 0, 0, 0, b'From: bre@klai.net')

    time.sleep(30)

    assert('<202109010003.181031O6020234@example.org>' in ms)
    assert('<202109010003.181031O6020234@example.com>' not in ms)
    assert(ms.rank_by_date[1000000] == (t1M // 30))
    assert(len(list(ms.rank_by_date.keys())) == 6)  # Including ghost for i1

    times = set([t1M // 30])
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
    time.sleep(30)
    ms.delete_everything(True, False, True)
