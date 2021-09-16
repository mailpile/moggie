import json
import traceback
import threading

from ..util.dumbcode import *

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

    def __init__(self, status_dir, backend, name=KIND):
        BaseWorker.__init__(self, status_dir, name=name)
        self.backend = backend
        self.functions.update({
            b'capabilities': (b'', self.api_capabilities),
            b'dump':   (b'',  self.api_dump),
            b'info':   (b'*', self.api_info),
            b'get':    (None, self.api_get),
            b'json':   (None, self.api_json),
            b'set':    (None, self.api_set),
            b'append': (None, self.api_append),
            b'delete': (None, self.api_delete)})

    def capabilities(self):
        return self.call('capabilities')

    def dump(self, compress=None):
        if compress is not None:
            return self.call('dump', qs={'compress': compress})
        return self.call('dump')

    def info(self, key=None, details=None):
        if details is not None:
            return self.call('info', key, qs={'details': details})
        return self.call('info', key)

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

    def api_capabilities(self):
        self.reply_json(self.backend.capabilities())

    def api_dump(self):
        self.reply(
            self.HTTP_200 + b'Content-Type: application/octet-stream',
            self.backend.dump())

    def api_info(self, key, details=False):
        self.reply_json(self.backend.info(key, details=details))

    def api_get(self, key, *args, dumbcode=False):
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
            sender = threading.Thread(target=sendit)
            sender.daemon = True
            sender.run()
        else:
            sendit()

    def api_json(self, *args):
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
                            nd.append(json.dumps(val).encode('utf-8'))
                        nd.append(b',')
                        data.extend(nd)
                    except:
                        traceback.print_exc()
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
        print('Setting %s = %s' % (key, dumb_decode(value)))
        self.backend.__setitem__(key, dumb_decode(value), **kwargs)
        self.reply_json({'set': key})

    def api_append(self, key, data):
        key = str(key, 'latin-1')
        if hasattr(self.backend, 'append'):
            self.backend.append(key, dumb_decode(data))
        else:
            self.backend[key] += dumb_decode(data)
        self.reply_json({'appended': key})

    def api_delete(self, key):
        key = str(key, 'latin-1')
        try:
            del self.backend[key]
        except KeyError:
            pass
        self.reply_json({'deleted': key})


if __name__ == '__main__':
    from ..storage.memory import CacheStorage as Storage

    objects = Storage({
        dumb_encode_asc(b'abc'): [1, 2, 3],
        dumb_encode_asc(b'efg'): "hello world",
        dumb_encode_asc(b'hij'): {1: 2},
        dumb_encode_asc(b'123'): b'{"foo": "bar"}',
        dumb_encode_asc(b'456'): b'0123456789abcdef'})
    print('%s' % objects.dict)

    sw = StorageWorker('/tmp', objects, name='lesid-test-storage').connect()
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

