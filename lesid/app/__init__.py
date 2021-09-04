from ..config.paths import DEFAULT_WORKDIR
from ..workers.app import AppWorker

def Main(args):
    wd = DEFAULT_WORKDIR()
    aw = AppWorker(wd).connect()
    try:
        print('Hello world: %s => %s' % (wd, aw))
        aw.join()
    except (AssertionError, KeyboardInterrupt):
        pass
    finally:
        pass
