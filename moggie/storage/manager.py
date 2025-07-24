# Brainstorming...
#
#   - Storage can be remote or local
#   - Storage is files and/or mailboxes (a .eml is a single-message mailbox)
#   - Copying from one storage to another is common, would be nice if it
#     were also performant
#   - Parsing file formats is a thing we like to parallelize
#
# ...
#
#   - Map containers (directory trees or mailboxes) to workers
#      - 1:n worker:containers
#      - One worker may handle multiple types of data if that's efficient?
#   - Storage manager may allow direct reads, but all writes go to a
#     worker to reduce odds of data corruption
#   - API should be fully async, forget the rest
#


class Credentials(dict):
    def __init__(self, *args, **kwargs):
        username = kwargs.pop('username', None)
        password = kwargs.pop('password', None)
        super().__init__(*args, **kwargs)
        if username is not None:
            self['username'] = username
        if password is not None:
            self['password'] = password

    username = property(
        lambda s: s['username'],
        lambda s, v: s.__setitem__('username', v))

    password = property(
        lambda s: s['password'],
        lambda s, v: s.__setitem__('password', v))


#
# spath1 = storage.path('/path/to/mailbox1', as_mailbox=True)
# spath2 = storage.path('/path/to/mailbox2', as_mailbox=True)
#
# with storage.group(spath1, spath2) as group:
#     async for metadata in spath1.mailbox():
#         # FIXME: Syntax for scheduling these as jobs?
#         await spath2.copy_email_from(spath1, metadata)
#


class StorageManager:
    def __init__(self, loop, workers):
        self.loop = loop
        self.workers = workers

    def group(self, *paths):
        # FIXME: Rearrange the paths so they all live on the same worker
        #        Return a context manager that releases the grouping when
        #        done.
        pass

    def path(self, key, credentials=None, as_mailbox=False, as_file=False):
        if as_mailbox:
            return StorageMailbox(self, key, credentials)
        if as_file:
            return StorageFile(self, key, credentials)
        return Storage(self, key, credentials)


class Storage:
    def __init__(self, manager, key, credentials=None):
        self.manager = manager
        self.creds = credentials
        self.key = key
        self.be = self._choose_backend(key, credentials)

    def _choose_backend(self, key, credentials):
        return 'FIXME: Filesystem backend'

    async def info(self, 
            details=None,
            recurse=0,
            credentials=None):
        pass

    async def read(self):
        pass

    async def write(self, data):
        pass

    async def delete(self):
        pass


class StorageFile(Storage):
    async def set(self, *args, dumbcode=None):
        pass

    async def append(self, value):
        pass


class StorageMailbox(Storage):
    async def mailbox(self,
            skip=0,
            limit=0,
            reverse=False,
            terms=None,
            sync_src=None,
            sync_dest=None):
        pass

    async def email(self, metadata,
            data=False,
            full_raw=False,
            parse=None):
        pass

    async def append_email(self, metadata, email_data):
        pass

    async def delete_emails(self, metadata_list):
        pass
