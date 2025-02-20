import asyncio
import msgpack
import sys
import time

from setproctitle import getproctitle, setproctitle

from moggie.util.dumbcode import dumb_decode, dumb_encode_bin
from moggie.util.intset import IntSet

from kettlingar import RPCKitten
from kettlingar.metrics import RPCKittenVarz

GLOBAL_UNIQUE_ID = False


class MoggieKitten(RPCKitten, RPCKittenVarz):
    class Configuration(RPCKitten.Configuration):
        APP_NAME = 'moggie'

    DOC_STRING_MAP = {}

    def get_docstring(self, method):
        if hasattr(method, '__name__'):
            name = method.__name__
        else:
            name = method.__class__.__name__
        return self.DOC_STRING_MAP.get(name) or super().get_docstring(method)

    def create_task(selff, task):
        return asyncio.get_event_loop().create_task(task)

    def to_msgpack(self, data):
        def _to_exttype(obj):
            if isinstance(obj, IntSet):
                return msgpack.ExtType(2, obj.dumb_encode_bin())
            raise TypeError('Unhandled data type: %s' % (type(obj).__name__,))
        return super().to_msgpack(data, default=_to_exttype)

    def from_msgpack(self, data):
        def _from_exttype(code, data):
            if code == 2:
                return IntSet.DumbDecode(data)
            return msgpack.ExtType(code, data)
        return super().from_msgpack(data, ext_hook=_from_exttype)

    @classmethod
    def IsProgress(self, result):
        return isinstance(result, dict) and result.get('_progress')

    def progress(self, fmt, **progress):
        progress['_progress'] = int(time.time())
        progress['_format'] = fmt
        if 'error' in progress:
            self.info(fmt % progress)
        else:
            self.debug(fmt % progress)
        return progress

    def print_result(self, result, print_raw=False, print_json=False):
        if self.IsProgress(result) and not (print_raw or print_json):
            sys.stderr.write(self.TextFormat(result) % result + '\n')
        else:
            return super().print_result(
                result,
                print_raw=print_raw,
                print_json=print_json)

    async def api_unique_app_id(self, request_info, set_id=None):
        """/unique_app_id [--set-id=<ID>]

        Fetch and optionally set the unique app ID.

        The unique app ID is used during mailbox copy/sync and in other
        cases where we want to be able to differentiate between artefacts
        created by this instance of moggie vs. another instance.

        This should normally only be done by the master moggie process.

        Returns:
            The current unique app ID.
        """
        global GLOBAL_UNIQUE_ID
        if set_id is not None:
            appn = self.config.app_name
            set_id = set_id if isinstance(set_id, str) else str(set_id, 'utf-8')
            GLOBAL_UNIQUE_ID = set_id
            if not appn.endswith('-' + GLOBAL_UNIQUE_ID):
                appn = '%s-%s' % (appn, GLOBAL_UNIQUE_ID)
                cpt2 = getproctitle().split('/', 1)[-1]
                setproctitle('%s/%s' % (appn, cpt2))
        return None, GLOBAL_UNIQUE_ID
