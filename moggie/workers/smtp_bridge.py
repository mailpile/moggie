"""
This is an SMTP proxy, which never queues mail to disk.

All incoming mail is instead forwarded directly over HTTPS or SMTP
to another server.

...

Not sure I intend to write this, but if I do, the goal is to make it
possible for people to switch to a @my-mailpile.is e-mail address,
without us actually having to store their data. For that we will need to
provide both SMTP/25 and submission services.

This can be relatively simple code, if we force the clients to send N
copies of an e-mail with N recipients, or quite complex if we try and
multiplex to save bytes on the network...

"""
import os
import json
import logging
import sys
import socket
import time

from upagekite.httpd import url
from upagekite.web import process_post

from ..config import AppConfig
from .public import PublicWorker, require


class SmtpBridgeSvcWorker(PublicWorker):
    """
    """

    KIND = 'smtp_bridge'
    NICE = 5  # Lower our priority

    CONFIG_SECTION = AppConfig.SMTP_BRIDGE_SVC

    def __init__(self, *args, **kwargs):
        PublicWorker.__init__(self, *args, **kwargs)

    def startup_tasks(self):
        pass


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.DEBUG)
    aw = SmtpBridgeSvcWorker.FromArgs('/tmp', sys.argv[1:])
    if aw.connect():
        try:
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
