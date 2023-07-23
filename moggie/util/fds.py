import logging
import os
import stat


PRIVATE_FDS = set()


def close_private_fds():
    """
    This will close any file descriptors which should not be inherited by
    our subprocesses.
    """
    closed = []
    for fno in range(0, 2048):
        try:
            f_st = os.fstat(fno)
            if stat.S_ISSOCK(f_st.st_mode) or (fno in PRIVATE_FDS):
                os.close(fno)
                closed.append(fno)
        except OSError:
            pass
    if closed:
        logging.debug('Closed private FDs: %s' % closed)
