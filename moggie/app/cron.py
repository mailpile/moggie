import copy
import datetime
import logging
import os
import shlex
import time
import threading

from moggie import Moggie
from ..storage.sqlite_zip import ZipEncryptedSQLite3
from ..util.friendly import friendly_date, friendly_datetime


class Cron:
    """
    An SQL-backed scheduler that supports crontab(5)-like rules and syntax.
    """

    def __init__(self, moggie, encryption_keys, eval_env=None):
        ext = 'sqz' if encryption_keys else 'sq3'
        path = os.path.join(moggie.work_dir, 'crontab.%s' % ext)
        self.db = ZipEncryptedSQLite3(path, encryption_keys=encryption_keys)
        self.configure_db()
        self.id_counter = int(1000 * time.time())

        self._moggie = moggie
        self._external_moggie = Moggie(moggie.work_dir)
        self._eval_env_extra = eval_env

        # FIXME: i18n? Allow other languages in crontab? Or no?
        self.dow = {
            'sun': 0, 'sunday': 0,
            'mon': 1, 'monday': 1,
            'tue': 2, 'tuesday': 2,
            'wed': 3, 'wednesday': 3,
            'thu': 4, 'thursday': 4,
            'fri': 5, 'friday': 5,
            'sat': 6, 'saturday': 6}
        self.months = {
            'jan': 1, 'january': 1,
            'feb': 2, 'february': 2,
            'mar': 3, 'march': 3,
            'apr': 4, 'april': 4,
            'may': 5,
            'jun': 6, 'june': 6,
            'jul': 7, 'july': 7,
            'aug': 8, 'august': 8,
            'sep': 9, 'september': 9, 'sept': 9,
            'oct': 10, 'october': 10,
            'nov': 11, 'november': 11,
            'dec': 12, 'december': 12}

    def configure_db(self):
        self.db.execute("""\
            CREATE TABLE IF NOT EXISTS crontab(
                id                   TEXT PRIMARY KEY,
                next_run             INTEGER,
                minutes              TEXT,
                hours                TEXT,
                month_days           TEXT,
                months               TEXT,
                weekdays             TEXT,
                action               TEXT,
                context              TEXT,
                source               TEXT)""")

    @classmethod
    def cronspec(self, spec, maxint, trans=None, add=0):
        """
        Expand a cron-style cronspec into a list of integers.

        A translation table can be provided to map words to ints (e.g. days
        of the week, or month names). Values >= maxint will wrap.

        >>> Cron.cronspec('*', 7)
        [0, 1, 2, 3, 4, 5, 6]

        >>> Cron.cronspec('1,2,3', 24)
        [1, 2, 3]

        >>> Cron.cronspec('1/5', 31, add=1)
        [1, 6, 11, 16, 21, 26, 31]

        >>> Cron.cronspec('1/3,17', 24)
        [1, 4, 7, 10, 13, 16, 17, 19, 22]

        >>> Cron.cronspec('sun,tue', 7, {'sun': 0, 'mon': 1, 'tue': 2})
        [0, 2]
        """
        if spec in ('', '*', None):
            return [d + add for d in range(0, maxint)]
        else:
            trans = trans or {}
            candidates = set()
            for part in str(spec).split(','):
                part = part.strip()
                if '/' in part:
                    start, step = part.split('/')
                    start = 0 if (start == '*') else start
                    start = int(trans.get(str(start).lower(), start))
                    step = int(step.strip())
                    for i in range(0, maxint, step):
                        candidate = (i + start) % (maxint + add)
                        if candidate >= add:
                            candidates.add(candidate)
                else:
                    candidate = int(trans.get(part, part)) % (maxint + add)
                    if candidate >= add:
                        candidates.add(candidate)

            return sorted(list(candidates))

    def calculate_next(self,
            minutes=None, hours=None, month_days=None, months=None,
            weekdays=None,
            now=None):
        """
        Calculate the next date to run a given event.

        >>> now = datetime.datetime(2023, 9, 29, 15, 0, 1)
        >>> crond.calculate_next('2/5', '14,16,18', now=now)
        datetime.datetime(2023, 9, 29, 16, 2)

        >>> nxt = crond.calculate_next('2/5', '14,16,18', '15', now=now)
        >>> nxt
        datetime.datetime(2023, 10, 15, 14, 2)
        >>> nxt = crond.calculate_next('2/5', '14,16,18', '15', now=nxt)
        >>> nxt
        datetime.datetime(2023, 10, 15, 14, 7)
        >>> nxt = crond.calculate_next('0', '14,16,18', '15', now=nxt)
        >>> nxt
        datetime.datetime(2023, 10, 15, 16, 0)

        >>> nxt = crond.calculate_next(0, 15, 15, 'jan,dec', now=nxt)
        >>> nxt
        datetime.datetime(2023, 12, 15, 15, 0)

        """
        from datetime import timedelta

        if isinstance(now, int):
            now = datetime.datetime.fromtimestamp(now)
        else:
            now = now or datetime.datetime.now()

        eq = True
        nxt = now.replace(second=0)
        minute = timedelta(minutes=1)

        loop_end = nxt + timedelta(days=366)

        allowed_minutes = self.cronspec(minutes, 60)
        allowed_hours = self.cronspec(hours, 24)
        allowed_weekdays = self.cronspec(weekdays, 7, trans=self.dow)
        allowed_month_days = self.cronspec(month_days, 31, add=1)
        allowed_months = self.cronspec(months, 12, add=1, trans=self.months)

        # This is a not very elegant or efficient; but it is easily
        # understood to give correct results. For frequent events the loop
        # won't get very far. Infrequent events take longer, but are also
        # infrequent. So, yay?
        while nxt < loop_end:
            nxt += minute
            if (nxt.minute in allowed_minutes
                    and nxt.hour in allowed_hours
                    and nxt.day in allowed_month_days
                    and nxt.month in allowed_months
                    and nxt.weekday() in allowed_weekdays):
                return nxt

        raise ValueError('No matching dates found, is spec valid?')

    def _delete_where(self, **kwargs):
        args, where = [], []
        for key, val in kwargs.items():
            args.append(val)
            where.append('%s = ?' % key.replace('_', ''))
        sql = 'DELETE FROM crontab WHERE ' + ' AND '.join(where)
        return self.db.execute(sql, args)

    def _log_schedule(self,
            _id, action, next_run, mins, hrs, mdays, mnths, wkdays):
        logging.debug('cron: scheduled %s for %d [%s %s %s %s %s] %s' % (
            _id, next_run,
            mins or '*', hrs or '*', mdays or '*', mnths or '*', wkdays or '*',
            action))

    def _eval_env(self):
        now_ts = int(time.time())
        now = datetime.datetime.now()
        env = {
            'yyyy_mm_dd': friendly_date(now_ts),
            'now_ts': now_ts,
            'now': now}
        if self._eval_env_extra:
            env.update(self._eval_env_extra)
        if 'moggie' not in env:
            env['moggie'] = self._external_moggie.connect()
        return env

    def _action_runner(self, _id, action, prefer_async=False):
        if action[:7] == 'moggie ':
            if prefer_async:
                async def _async_runner():
                    nonlocal _id, action, action
                    args = shlex.split(action % self._eval_env())[1:]
                    logging.info('cron(%s/moggie): %s' % (_id, args))
                    await self._moggie.connect().async_run(*args)
                return (True, _async_runner)
            else:
                def _runner():
                    nonlocal _id, action, action
                    args = shlex.split(action % self._eval_env())[1:]
                    logging.info('cron(%s/moggie): %s' % (_id, args))
                    self._external_moggie.connect().run(*args)

        elif action[:1] in ('!', '~', os.path.sep):
            action = action.lstrip('!')
            def _runner():
                nonlocal _id, action
                action = action % self._eval_env()
                logging.info('cron(%s/sh): %s' % (_id, action))
                os.system(action)

        else:
            def _runner():
                nonlocal _id, action
                logging.info('cron(%s/py): %s' % (_id, action))
                eval(action, self._eval_env())

        if prefer_async:
            return (False, _runner)
        return _runner

    def _run_action(self, _id, action, runner=None):
        if runner is None:
            runner = self._action_runner(_id, action)
        def _thread_runner():
            nonlocal _id, runner
            try:
                runner()
            except KeyboardInterrupt:
                raise
            except:
                logging.exception('cron(%s) FAILED' % (_id,))

        at = threading.Thread(target=_thread_runner)
        at.daemon = True
        at.start()
        return at

    async def _async_run_action(self, _id, action):
        is_async, _runner = self._action_runner(_id, action, prefer_async=True)
        if is_async:
            await _runner()
        else:
            return self._run_action(_id, action, runner=_runner)

    def _yield_due_and_reschedule(self, context, now):
        now = int(now or time.time())

        sql = """
            SELECT id, action, minutes, hours, month_days, months, weekdays
              FROM crontab
             WHERE next_run < ?"""
        if context:
            sql += ' AND context = ?'
            args = (now, context)
        else:
            args = (now,)

        for due in self.db.execute(sql, args):
            _id, action, mins, hrs, mdays, mnths, wkdays = due
            yield _id, action

            # Deleting or rescheduling the event happens AFTER it has been
            # yielded; if our caller crashes while handling the event and
            # never comes back, the current event stays scheduled.
            if mins or hrs or mdays or mnths or wkdays:
                next_run = self.calculate_next(
                    mins, hrs, mdays, mnths, wkdays, now=now).timestamp()
                self.db.execute("""
                    UPDATE crontab
                       SET next_run = ?
                     WHERE id = ?""", (next_run, _id))
                self._log_schedule(
                    _id, action, next_run, mins, hrs, mdays, mnths, wkdays)
            else:
                self._delete_where(_id=_id)

    def run_scheduled(self, context=None, join=True, now=None):
        threads = []
        for _id, action in self._yield_due_and_reschedule(context, now):
            threads.append(self._run_action(_id, action))
        if join:
            for at in threads:
                at.join()
        self.db.save()

    async def async_run_scheduled(self, context=None, join=True, now=None):
        threads = []
        for _id, action in self._yield_due_and_reschedule(context, now):
            threads.append(await self._async_run_action(_id, action))
        if join:
            for at in threads:
                if at is not None:
                    at.join()
        self.db.save()

    def schedule_action(self, action,
            minutes=None, hours=None, month_days=None, months=None,
            weekdays=None, context=None, source=None,
            _id=None, next_run=None,
            save=True):
        if next_run:
            if minutes or hours or month_days or months or weekdays:
                raise ValueError(
                    'Please use next_run or a time specification, not both')
        else:
            next_run = self.calculate_next(
                minutes, hours, month_days, months, weekdays)
        if not _id:
            _id = '%x' % self.id_counter
            self.id_counter += 1

        if hasattr(next_run, 'timestamp'):
            next_run = next_run.timestamp()
        self.db.execute("""\
            INSERT INTO crontab(id, action, next_run,
                                minutes, hours, month_days, months, weekdays,
                                context, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            _id, action, int(next_run),
            minutes, hours, month_days, months, weekdays,
            context, source))

        if save:
            self.db.save()
        self._log_schedule(
            _id, action, next_run, minutes, hours, month_days, months, weekdays)

    def parse_crontab(self, crontext, source='crontab'):
        self._delete_where(source=source)
        for line in crontext.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            mm, hh, d, m, wd, action = line.split(None, 5)
            self.schedule_action(action,
                minutes=mm, hours=hh, month_days=d, months=m, weekdays=wd,
                source=source,
                save=False)
        self.db.save()

