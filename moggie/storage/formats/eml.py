import copy
import logging
import email.utils
from ...email.util import quick_msgparse

from .mbox import FormatMbox


class FormatEml(FormatMbox):
    NAME = 'eml'
    TAG = b'eml'

    @classmethod
    def Magic(cls, parent, key, is_dir=None):
        try:
            if is_dir:
                return False
            if parent[key][:5] == b'From ':
                return False
            return cls.IsEmail(parent[key])
        except (KeyError, OSError):
            return False

    def _find_message_offsets(self, key):
        (b, wanted_hash) = self._key_to_range_hash(key)
        if b != 0:
            raise KeyError('Invalid offset for EML')

        if not self.IsEmail(self.container[:16*1024]):
            raise KeyError('Message not found')

        # Verify that we have the correct message
        hend, hdrs = quick_msgparse(self.container, 0)
        if self.RangeToKey(b, _data=hdrs) != key:
            raise KeyError('Message not found')

        return 0, len(self.container)

    def iter_email_offsets(self, skip=0, deleted=False):
        obj = self.container
        try:
            hend, hdrs = quick_msgparse(obj, 0)
            rank = 1
            end = len(obj)-1
            yield 0, hend, end+1, hdrs, rank
        except (ValueError, TypeError):
            return


if __name__ == "__main__":
    print('Tests passed OK')
