# Helpers for doing async things

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
