import logging
import time
import traceback
import threading

from ..api.exceptions import NeedInfoException
from ..util.dumbcode import *
from ..util.mailpile import PleaseUnlockError
from ..email.metadata import Metadata

from .base import BaseWorker


class StorageWorker(BaseWorker):
    """
    GET /capabilities

        Returns a JSON object describing the actual capabilities
        of this storage server.

    GET /info/<key>

        Returns a JSON object describing a given key. Contents vary
        from one backend to another.

    GET /get/<key>[/d<start>[/d<end>]]

        Streams the contents of a single key as binary data.
        Ranges for partial downloads are supported.

    GET /json/<key>[/<key2> .. /<keyN>]
    POST /json/*

        Returns a JSON object containing keys and values for any of the
        requested keys found in the argument list. The data must itself
        be valid JSON, or the output will not parse. Keys will be encoded
        using "dumbcode" to preserve their types.

    POST /set/<key>

        Update the contents of a single key.

    POST /append/<key>

        Append to a single key. This method uses Python's + operator, so
        it can also be used to increment or decrement integer values. If
        types do not match, errors will result.

    POST /del/<key>

        Delete a key and the associated data.
    """

    KIND = 'storage'

    PEEK_BYTES = 8192
    BLOCK = 8192

    PARSE_CACHE_TTL = 180

    def __init__(self, status_dir, backend,
            name=KIND, notify=None, log_level=logging.ERROR):
        BaseWorker.__init__(self, status_dir,
            name=name, notify=notify, log_level=log_level)
        self.backend = backend
        self.functions.update({
            b'capabilities': (True,  self.api_capabilities),
            b'dump':         (True,  self.api_dump),
            b'info':         (True,  self.api_info),
            b'mailbox':      (True,  self.api_mailbox),
            b'email':        (True,  self.api_email),
            b'get':          (False, self.api_get),
            b'json':         (False, self.api_json),
            b'set':          (False, self.api_set),
            b'append':       (False, self.api_append),
            b'delete':       (False, self.api_delete)})

        self.parsed_mailboxes = {}
        self.background_thread = None

    def _expire_parse_cache(self):
        et = time.time() - self.PARSE_CACHE_TTL
        expired = [k for k, v in self.parsed_mailboxes.items() if v[0] <= et]
        for key in expired:
            del self.parsed_mailboxes[key]

    def _background(self, task):
        if self.background_thread is not None:
            self.background_thread.join()
        self.background_thread = threading.Thread(target=task)
        self.background_thread.daemon = True
        self.background_thread.start()

    def capabilities(self):
        return self.call('capabilities')

    def dump(self, compress=None):
        if compress is not None:
            return self.call('dump', qs={'compress': compress})
        return self.call('dump')

    async def async_info(self, loop,
            key=None, details=None, recurse=0, relpath=None):
        return await self.async_call(loop,
            'info', key, details, recurse, relpath)

    def info(self, key=None, details=None, recurse=0, relpath=None):
        return self.call('info', key, details, recurse, relpath)

    async def async_mailbox(self, loop, key,
            skip=0, limit=None, username=None, password=None):
        return await self.async_call(loop,
            'mailbox', key, skip, limit, username, password,
            hide_qs=True)  # Keep passwords out of web logs

    def mailbox(self, key, skip=0, limit=None, username=None, password=None):
        return self.call(
            'mailbox', key, skip, limit, username, password,
            hide_qs=True)  # Keep passwords out of web logs

    def get(self, key, *args, dumbcode=None):
        if dumbcode is not None:
            return self.call('get', key, *args, qs={'dumbcode': dumbcode})
        return self.call('get', key, *args)

    def json(self, *keys):
        return self.call('json', *keys)

    def set(self, key, value, **kwargs):
        return self.call('set', key, value, **kwargs)

    def append(self, key, value):
        return self.call('append', key, value)

    def delete(self, key):
        return self.call('delete', key)

    def api_capabilities(self, **kwargs):
        self.reply_json(self.backend.capabilities())

    def api_dump(self, **kwargs):
        self.reply(
            self.HTTP_200 + b'Content-Type: application/octet-stream',
            self.backend.dump())

    def api_info(self, key, details, recurse, relpath, method=None):
        self.reply_json(
            self.backend.info(key,
                details=details, recurse=recurse, relpath=relpath))

    def api_mailbox(self, key, skip, limit, username, password, method=None):
        self._expire_parse_cache()
        if key in self.parsed_mailboxes:
            while (not self.parsed_mailboxes[key][1]
                    and self.background_thread is not None):
                time.sleep(0.1)
            logging.debug('%s: Returning from self.parsed_mailboxes' % key)
            pm = self.parsed_mailboxes[key][-1]
            beg = skip
            end = skip + (limit or (len(pm)-skip))
            return self.reply_json(pm[beg:end])

        try:
            collect = []
            parser = self.backend.iter_mailbox(key,
                skip=skip, username=username, password=password)
            parse_cache = [time.time(), False, collect]

            if limit is None:
                collect.extend(msg for msg in parser)
                logging.debug(
                    '%s: Returning %d messages (u)' % (key, len(collect)))
                parse_cache[1] = True
                self.parsed_mailboxes[key] = parse_cache
                return self.reply_json(collect)

            result = []
            for msg in parser:
                collect.append(msg)
                if limit and len(result) >= limit:
                    break
                result.append(msg)

            logging.debug('%s: Returning %d messages' % (key, len(result)))
            self.parsed_mailboxes[key] = parse_cache
            self.reply_json(result)

        except PleaseUnlockError as pue:
            logging.debug('Need unlock, raising NeedInfoException')
            needs, neo = [], NeedInfoException
            if pue.username:
                needs.append(neo.Need('Username', 'username'))
            if pue.password:
                needs.append(
                    neo.Need('Password', 'password', datatype='password'))
            raise neo(str(pue), need=needs)

        # Finish in background thread
        if limit and len(result) >= limit:
            def finish():
                logging.debug('%s: Background completing scan' % key)
                collect.extend(msg for msg in parser)
                self.parsed_mailboxes[key][1] = True
                self.background_thread = None
            self._background(finish)
        else:
            self.parsed_mailboxes[key][1] = True

    async def async_email(self, loop, metadata,
            text=False, data=False, full_raw=False, parts=None):
        return await self.async_call(loop, 'email',
            metadata[:Metadata.OFS_HEADERS], text, data, full_raw, parts)

    def email(self, metadata,
            text=False, data=False, full_raw=False, parts=None):
        return self.call('email',
            metadata[:Metadata.OFS_HEADERS], text, data, full_raw, parts)

    def api_email(self, metadata, text, data, full_raw, parts, method=None):
        metadata = Metadata(*(metadata[:Metadata.OFS_HEADERS] + [b'']))
        parsed = self.backend.parse_message(metadata)
        if text:
            parsed.with_text()
        if data or parts:
            parsed.with_data(only=(parts or None))
        if full_raw:
            parsed.with_full_raw()
        self.reply_json(parsed)

    def api_get(self, key, *args, dumbcode=False, method=None):
        if len(args) > 0 and dumbcode:
            return self.reply(self.HTTP_400)
        try:
            key = str(key, 'latin-1')
            if dumbcode:
                value = dumb_encode_bin(self.backend[key])
            else:
                value = self.backend[key]
            begin = dumb_decode(args[0]) if (len(args) > 0) else  0
            length = len(value)
            length = min(length, dumb_decode(args[1]) if (len(args) > 1) else length)
            length -= begin
        except KeyError:
            return self.reply(self.HTTP_404)
        except (IndexError, ValueError, TypeError):
            return self.reply(self.HTTP_400)

        c = self.start_sending_data('application/octet-stream', length)
        def sendit():
            p = begin
            for chunk in range(0, 1 + length//self.BLOCK):
                c.send(value[p:min(p+self.BLOCK, begin+length)])
                p += self.BLOCK
            self._client.close()

        if length > self.BLOCK * 5:
            self._background(sendit)
        else:
            sendit()

    def api_json(self, *args, **kwargs):
        """
        This function assumes our backend data is already JSON formatted
        and we are just serving up a dictionary of results.
        """
        try:
            data = [b'{']
            for key in args:
                key = str(key, 'latin-1')
                if key in self.backend:
                    val = self.backend.get(key)
                    try:
                        nd = [('"%s":' % key).encode('latin-1')]
                        if isinstance(val, bytes):
                            nd.append(val)
                        elif isinstance(val, str):
                            nd.append(('"%s"' % val).encode('utf-8'))
                        else:
                            nd.append(to_json(val).encode('utf-8'))
                        nd.append(b',')
                        data.extend(nd)
                    except:
                        logging.exception('api_json failed to encode data')
            if len(data) > 1:
                data[-1] = b'}\n'
                data = b''.join(data)
            else:
                data = b'{}\n'
        except IndexError:
            return self.reply(self.HTTP_400)
        except KeyError:
            return self.reply(self.HTTP_404)

        length = len(data)
        c = self.start_sending_data('application/json', length)
        p = 0
        for chunk in range(0, 1 + length//self.BLOCK):
            c.send(data[p:p+self.BLOCK])
            p += self.BLOCK
        self._client.close()

    def api_set(self, key, value, **kwargs):
        key = str(key, 'latin-1')
        #print('Setting %s = %s' % (key, dumb_decode(value)))
        self.backend.__setitem__(key, dumb_decode(value), **kwargs)
        self.reply_json({'set': key})

    def api_append(self, key, data, method=None):
        key = str(key, 'latin-1')
        if hasattr(self.backend, 'append'):
            self.backend.append(key, dumb_decode(data))
        else:
            self.backend[key] += dumb_decode(data)
        self.reply_json({'appended': key})

    def api_delete(self, key, method=None):
        key = str(key, 'latin-1')
        try:
            del self.backend[key]
        except KeyError:
            pass
        self.reply_json({'deleted': key})


if __name__ == '__main__':
    from ..storage.memory import CacheStorage as Storage
    logging.basicConfig(level=logging.DEBUG)

    objects = Storage({
        dumb_encode_asc(b'abc'): [1, 2, 3],
        dumb_encode_asc(b'efg'): "hello world",
        dumb_encode_asc(b'hij'): {1: 2},
        dumb_encode_asc(b'123'): b'{"foo": "bar"}',
        dumb_encode_asc(b'456'): b'0123456789abcdef'})
    print('%s' % objects.dict)

    sw = StorageWorker('/tmp', objects, name='moggie-test-storage').connect()
    if sw:
        try:
            print(sw.capabilities())
            print(sw.info(details=True))
            print(sw.info(b'123'))
            print(sw.info(b'abc'))
            print('%s' % sw.json(b'abc', b'efg', b'123'))

            hdr, fd = sw.get(b'456', 3, 6)
            assert(3 == int(sw.parse_header(hdr)['Content-Length']))
            assert(b'345' == fd.read())

            try:
                sw.get(99)
                assert(not 'reached')
            except PermissionError:  #FIXME
                pass

            print('** Tests passed, waiting... **')
            sw.join()
        finally:
            sw.terminate()

