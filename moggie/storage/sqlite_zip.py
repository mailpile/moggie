# Helper for reading/writing sqlite3 databases from/to encrypted ZIP files.
import datetime
import logging
import threading
import time
import sqlite3
import pyzipper as zipfile


class ZipEncryptedSQLite3:
    def __init__(self, filepath,
             encryption_keys=None,
             save_check_interval=10,
             save_min_interval=60):

        self.db_filepath = filepath
        self.db_lock = threading.Lock()

        self.password = encryption_keys[0] if encryption_keys else None
        if isinstance(self.password, str):
            self.password = bytes(self.password, 'utf-8')

        if filepath.endswith('.sq3'):
             self.in_memory = False
             self.db = sqlite3.connect(filepath)
        elif filepath.endswith('.sqz'):
             self.in_memory = True
             self.load(encryption_keys)
        else:
             raise ValueError('What kind of file is %s?' % filepath)

        self.save_next = 0
        self.save_check_interval = save_check_interval
        self.save_min_interval = save_min_interval
        self.save_worker = None
        self.saved_at = self.db.total_changes

    def execute(self, *args, **kwargs):
        with self.db_lock:
            rv = self.db.execute(*args, **kwargs)
            self.db.commit()
            return rv

    def start_background_saver(self):
        if not self.in_memory or not self.db:
            return False
        if self.save_worker is not None:
            raise RuntimeError(
                'sqlite_zip(%s): DB save worker is already running'
                % (self.db_filepath,))

        def save_worker():
            logging.info(
                'sqlite_zip(%s): Started background saver'
                % (self.db_filepath,))
            try:
                while self.db is not None:
                    time.sleep(self.save_check_interval)
                    if (self.db
                             and self.db.total_changes != self.saved_at
                             and time.time() > self.save_next):
                        logging.debug(
                            '[sqlite_zip] Background save at %d changes: %s'
                            % (self.db.total_changes, self.db_filepath))
                        self.save()
                        self.save_next = int(
                            time.time() + self.save_min_interval)
            finally:
                self.save_worker = None

        self.save_worker = threading.Thread(target=save_worker)
        self.save_worker.daemon = True
        self.save_worker.start()
        return True

    def load(self, encryption_keys):
        if not self.in_memory:
            return
        with self.db_lock:
            self.db = sqlite3.connect(':memory:', check_same_thread=False)
            self.saved_at = self.db.total_changes

            fn = data = None
            try:
                with open(self.db_filepath, 'rb') as fd:
                    zf = zipfile.AESZipFile(fd, mode='r')
                    for try_fn in ('sqlite.sql', 'sqlite.sq3'):
                        for key in encryption_keys:
                            zf.setpassword(key)
                            try:
                                data = zf.open(try_fn).read()
                                fn = try_fn
                                break
                            except KeyError:
                                break
            except (OSError, IOError): 
                pass

            if fn and data:
                if fn == 'sqlite.sql':
                    self.db.executescript(str(data, 'utf-8'))
                    self.saved_at = self.db.total_changes
                elif ifn == 'sqlite.sq3':
                    self.db.deserialize(data)
                    self.saved_at = self.db.total_changes

    def save(self):
        if not self.in_memory or not self.db:
            return False
        with self.db_lock:
            if not self.db:
                return False
            if self.saved_at == self.db.total_changes:
                return False

            self.saved_at = self.db.total_changes
            if hasattr(self.db, 'serialize'):
                fn, data = 'sqlite.sq3', self.db.serialize()
            else:
                fn, data = 'sqlite.sql', '\n'.join(self.db.iterdump())

            with open(self.db_filepath, 'wb') as fd:
                zf = zipfile.AESZipFile(fd,
                    compression=zipfile.ZIP_DEFLATED,
                    mode='w')
                zf.setpassword(self.password)
                zf.setencryption(zipfile.WZ_AES, nbits=256)

                tt = datetime.datetime.now().timetuple()
                fi = zf.zipinfo_cls(filename=fn, date_time=tt)
                fi.external_attr = 0o000640 << 16
                fi.compress_type = zipfile.ZIP_DEFLATED

                zf.writestr(fi, data)
                zf.close()

            logging.debug('[sqlite_zip] Saved %s' % (self.db_filepath,))

        return True

    def close(self):
        if not self.db:
            return False
        changed = self.save()
        with self.db_lock:
            self.db.close()
            self.db = None
        return changed


if __name__ == '__main__':
    import os, time
    FN1 = '/tmp/test-%d.sq3' % time.time()
    FN2 = '/tmp/test-%d.sqz' % time.time()
    for f in (FN1, FN2):
        if os.path.exists(f):
            os.remove(f)

    sq3 = ZipEncryptedSQLite3(FN1)
    sqz = ZipEncryptedSQLite3(FN2, encryption_keys=[b'1234'])

    for db in (sq3, sqz):
        assert(db.in_memory == (db == sqz))
        assert(db.start_background_saver() == db.in_memory)
        if db.in_memory:
            try:
                db.start_background_saver()
                assert(not 'reached')
            except RuntimeError:
                pass

        db.db.execute("""\
            CREATE TABLE IF NOT EXISTS testing(
                key     TEXT PRIMARY KEY,
                value   TEXT)""")

        db.db.execute("DELETE FROM testing")
        db.db.execute("""\
            INSERT INTO testing(key, value) VALUES (?, ?)""",
            ('bjarni', 'iceland'))
        db.db.execute("""\
            INSERT INTO testing(key, value) VALUES (?, ?)""",
            ('alice', 'wonderland'))
        db.db.execute("""\
            INSERT INTO testing(key, value) VALUES (?, ?)""",
            ('bob', 'brexitland'))

        assert(db.save() == db.in_memory)  # Changed, if in memory
        assert(not db.save())              # No changes
        assert(not db.close())             # No changes

    sqz2 = ZipEncryptedSQLite3(FN2, encryption_keys=[b'1234'])
    rows = list(sqz2.db.execute("""SELECT * FROM testing"""))
    assert(rows[0][0] == 'bjarni')
    assert(rows[1][1] == 'wonderland')
    assert(rows[2][0] == 'bob')
    assert(not sqz2.save())   # No changes!
    assert(not sqz2.close())  # No changes!

    print('Tests passed OK')
    for f in (FN1, FN2):
        if os.path.exists(f):
            os.remove(f)
