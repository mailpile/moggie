from zipfile import *

# So, standardizing as Zipped Maildirs as a mailbox format implies a few
# things.
#
#   * Do we want to STORE mails so we can mmap within the archive?
#   * Do we want to implement Zip encryption? Zip AES encryption?
#   * We need to be able to erase from the archive.
#   * It would be nice to be able to reconstruct the zip index if it
#     gets corrupted/omitted during a write+crash.
#

class FancyZipFile(ZipFile):
    pass

    def writestr(self, *args, **kwargs):
        return super().writestr(*args, **kwargs)
        # FIXME: Make sure the index always gets written? Does zipfile already
        #        do this?

    def read_or_map(self, name, pwd=None):
        # FIXME: If the file is stored uncompressed, return a mmap slice.
        return super().read(name, pwd=pwd)

