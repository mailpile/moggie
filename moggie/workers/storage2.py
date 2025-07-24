import os

from pagekite2.util.worker import Worker


class Storage2(Worker):
    """
    This is a moggie storage worker. There should be a pool of these!


    """
    class Configuration(Worker.Configuration):
        APP_NAME = 'moggie'
        WORKER_NAME = 'storage2'

        def __init__(self):
            super().__init__()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._imap_cls = None
        self._fs = None

    def fs(self):
        if not self._fs:
            from moggie.storage.files import FileStorage
            self._fs = FileStorage(relative_to=os.path.expanduser('~'))
        return self._fs

    def imap(self):
        if not self._imap_cls:
            from moggie.storage.imap import ImapStorage
            self._imap_cls = ImapStorage
        return self._imap_cls

    async def init_server(self, server):
        return server
 
    async def api_info(self, m, h, b, key, recurse=0):
        return None, self.fs().info(key,
            details=True,
            recurse=int(recurse),
            relpath=False)

    async def api_mailbox(self, m, h, b, key,
            terms=None,
            skip=0,
            limit=None,
            reverse=False,
            cached=True,
            sync_src=None,
            sync_dest=None):
        pass


if __name__ == '__main__':
    import sys
    Storage2.Main(sys.argv[1:])
