import doctest
import os
import shlex
import sys
import time
import unittest

import moggie.app.cron

from moggie import Moggie
from moggie.app.cron import Cron


class MoggieCronTests(unittest.TestCase):
    def test_cron(self):
        tmpdir = os.path.join(os.path.dirname(__file__), '..', 'tmp')
        testfile = os.path.join(tmpdir, 'moggie.cron.test')
        testsqz = os.path.join(tmpdir, 'crontab.sqz')

        os.system(shlex.join(['rm',  '-f', testfile, testsqz]))
        history = []

        now = int(time.time())
        mog = Moggie(tmpdir)
        env = {'history': history, 'moggie': mog, 'testfile': testfile}
        crond = Cron(mog, [b'1234123412341234'], eval_env=env)

        for times in (1, 2):
            # Parse it twice; this guarantees that we only ever keep one
            # crontab worth of events in the schedule.
            crond.parse_crontab("""\
# This is a test, comment
45  6,18  * * *  flag: history.append('hello')  # Test Python code
*/5    *  * * *  history.append('world')        # More Python, diff schedule
00    00  * * *  /usr/bin/touch "%(testfile)s"  # Test shell commands
00    00  * * *  history.append(moggie.help())  # Moggie in Python
""" % env)

        for hour in range(0, 25):
            crond.run_scheduled(now=now + 300 + hour*3600)

        # Did help get rendered?
        self.assertEqual(1,
             sum(1 for e in history if isinstance(e, list) and e[0]['text']))

        # The 6am / 6pm event should run twice
        self.assertEqual(2, sum(1 for e in history if e == 'hello'))

        # The every-five-minute even should run each time
        self.assertEqual(25, sum(1 for e in history if e == 'world'))

        # Make sure that the expected files got created
        self.assertTrue(os.path.exists(testfile))
        self.assertTrue(os.path.exists(testsqz))

        results = doctest.testmod(moggie.app.cron,
            optionflags=doctest.ELLIPSIS,
            extraglobs={'crond': crond})
        if results.failed:
            print('%s' % (results,))
        self.assertFalse(results.failed)

        os.system(shlex.join(['rm',  '-f', testfile, testsqz]))

