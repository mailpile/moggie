# Helpers for doing async things
import asyncio
import logging
import threading
import time


class AsyncProxyObject:
    def __init__(self, cls, arg_filter=None):
        def wrap(cls, func):
            async def wrapper(*args, **kwargs):
                if arg_filter is not None:
                    args = arg_filter(args)
                    kwargs = arg_filter(kwargs)
                return getattr(cls, func)(*args, **kwargs)
            return wrapper
        for func in (f for f in dir(cls) if not f[:1] == '_'):
            setattr(self, func, wrap(cls, func))


async def async_run_in_thread(method, *m_args, **m_kwargs):
    def runner(l, q):
        time.sleep(0.1)
        try:
            rv = method(*m_args, **m_kwargs)
        except:
            logging.exception('async in thread crashed, %s' % (method,))
            rv = None
        l.call_soon_threadsafe(q.put_nowait, rv)

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue()
    thr = threading.Thread(target=runner, args=(loop, queue))
    thr.daemon = True
    thr.start()
    return await queue.get()


def run_async_in_thread(method, *m_args, **m_kwargs):
    result = []
    def runner():
        result.append(asyncio.run(method(*m_args, **m_kwargs)))
    thr = threading.Thread(target=runner)
    thr.daemon = True
    thr.start()
    thr.join()
    return result[0]
