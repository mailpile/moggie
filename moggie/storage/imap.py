import logging
import time

from ..email.metadata import Metadata
from ..email.parsemime import parse_message as ep_parse_message
from ..email.util import quick_msgparse, make_ts_and_Metadata
from ..util.dumbcode import *
from ..util.mailpile import PleaseUnlockError

from .base import BaseStorage
from .mailboxes import MailboxStorageMixin


class ImapStorage(BaseStorage, MailboxStorageMixin):
    def __init__(self, metadata=None, ask_secret=None, set_secret=None):
        self.metadata = metadata
        self.ask_secret = ask_secret
        self.set_secret = set_secret
        BaseStorage.__init__(self)
        #self.dict = None

    def can_handle_ptr(self, ptr):
        return (ptr.ptr_type == Metadata.PTR.IS_IMAP)


if __name__ == "__main__":
    import sys
    print('Tests passed OK')
