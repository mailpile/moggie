import datetime
import logging
import os
import time
import threading

from ..storage.sqlite_zip import ZipEncryptedSQLite3


class Cron:
    """
    An SQL-backed scheduler that supports crontab(5)-like rules and syntax.
    """

    def __init__(self, data_directory, encryption_keys, eval_env=None):
        ext = 'sqz' if encryption_keys else 'sq3'
        path = os.path.join(data_directory, 'crontab.%s' % ext)
        self.db = ZipEncryptedSQLite3(path, encryption_keys=encryption_keys)
        self.configure_db()
        self.id_counter = int(1000 * time.time())
        self.eval_env = globals() if (eval_env is None) else eval_env

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

    def _run_action(self, _id, action):
        def _runner():
            nonlocal _id, action
            try:
                if action.startswith('moggie '):
                    logging.info('FIXME! cron(%s): %s' % (_id, action))

                elif action[:1] in ('!', '~', os.path.sep):
                    action = action.lstrip('!')
                    logging.info('cron(%s/shell): %s' % (_id, action))
                    os.system(action)

                else:
                    logging.info('cron(%s/python): %s' % (_id, action))
                    eval(action, self.eval_env)

            except KeyboardInterrupt:
                raise
            except:
                logging.exception('cron(%s) FAILED' % (_id,))

        at = threading.Thread(target=_runner)
        at.daemon = True
        at.start()
        return at

    def run_scheduled(self, context=None, join=True, now=None):
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

        threads = []
        for due in self.db.execute(sql, args):
            _id, action, mins, hrs, mdays, mnths, wkdays = due
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

            threads.append(self._run_action(_id, action))
        if join:
            for at in threads:
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

