import os
import sys

from .. import platforms
from . import *


try:
    from appdirs import AppDirs
except ImportError:
    AppDirs = None


def _ensure_exists(path, mode=0o700):
    if not os.path.exists(path):
        head, tail = os.path.split(path)
        _ensure_exists(head)
        os.mkdir(path, mode)
    return path


def user_mail_spool():
    import getpass
    try:
        spool = os.path.join('/var/mail', getpass.getuser())
        if os.path.exists(spool):
            return spool
    except (OSError, IOError, TypeError):
        return None


def LEGACY_DEFAULT_WORKDIR(profile, appname=APPNAME, appname_uc=APPNAME_UC):
    if profile == 'default':
        # Backwards compatibility: If the old ~/.mailpile exists, use it.
        workdir = os.path.expanduser('~/.%s' % appname)
        if os.path.exists(workdir) and os.path.isdir(workdir):
            return workdir

    return os.path.join(
        platforms.GetAppDataDirectory(), appname_uc, profile)


def DEFAULT_WORKDIR(
        app='MOGGIE', appname=APPNAME, appname_uc=APPNAME_UC,
        create=True, workdir=None, profile='default', check_env=True):
    if create:
        _found = _ensure_exists
    else:
        _found = lambda d: d

    # The Mailpile environment variable trumps everything
    if check_env:
        workdir = os.getenv('%s_HOME' % app)
        if workdir:
            return _found(workdir)

    # Which profile?
    if check_env:
        profile = os.getenv('%s_PROFILE' % app, profile)

    # Check if we have a legacy setup we need to preserve
    workdir = LEGACY_DEFAULT_WORKDIR(profile, appname, appname_uc)
    if not AppDirs or (os.path.exists(workdir) and os.path.isdir(workdir)):
        return _found(workdir)

    # Use platform-specific defaults
    # via https://github.com/ActiveState/appdirs
    dirs = AppDirs(appname_uc, "Mailpile ehf")
    return _found(os.path.join(dirs.user_data_dir, profile))


def DEFAULT_SHARED_DATADIR():
    # IMPORTANT: This code is duplicated in mailpile-admin.py.
    #            If it needs changing please change both places!
    env_share = os.getenv('MOGGIE_SHARED')
    if env_share is not None:
        return env_share

    # Check if we are running in a virtual env
    # http://stackoverflow.com/questions/1871549/python-determine-if-running-inside-virtualenv
    # We must also check that we are installed in the virtual env,
    # not just that we are running in a virtual env.
    if ((hasattr(sys, 'real_prefix') or hasattr(sys, 'base_prefix'))
            and __file__.startswith(sys.prefix)):
        return os.path.join(sys.prefix, 'share', APPNAME)

    # Check if we've been installed to /usr/local (or equivalent)
    usr_local = os.path.join(sys.prefix, 'local')
    if __file__.startswith(usr_local):
        return os.path.join(usr_local, 'share', APPNAME)

    # Check if we are in /usr/ (sys.prefix)
    if __file__.startswith(sys.prefix):
        return os.path.join(sys.prefix, 'share', APPNAME)

    # Else assume dev mode, source tree layout
    return os.path.join(
        os.path.dirname(__file__), '..', '..', 'shared-data')


def DEFAULT_LOCALE_DIRECTORY():
    """Get the gettext translation object, no matter where our CWD is"""
    return os.path.join(DEFAULT_SHARED_DATADIR(), "locale")


def LOCK_PATHS(workdir=None):
    if workdir is None:
        workdir = DEFAULT_WORKDIR()
    return (
        os.path.join(workdir, 'public-lock'),
        os.path.join(workdir, 'workdir-lock'))


def mail_path_suggestions(
        config=None, context=None,
        local=False, mailpilev1=True, thunderbird=True):

    found = set()
    paths = []
    def _p(src, val, must_exist=False):
        if val is not None:
            info = {'src': src, 'path': val, 'exists': os.path.exists(val)}
            if (not must_exist) or info.get('exists'):
                if val not in found:
                    paths.append(info)
                    found.add(val)
                return True
        return False

    # FIXME: Extract thing from our own config file!
    #        IMAP accounts, folders with mailboxes, ...
    if not local:
        return paths

    _p('spool', user_mail_spool(), must_exist=True)
    _p('home', os.path.expanduser('~'), must_exist=True)
    _p('home', os.path.expanduser('~/mail'), must_exist=True)
    _p('home', os.path.expanduser('~/Mail'), must_exist=True)
    _p('home', os.path.expanduser('~/Maildir'), must_exist=True)

    # Do we have an old Mailpile lying around?
    if mailpilev1:
        mailpilev1 = DEFAULT_WORKDIR(
            app='MAILPILE', appname='mailpile', appname_uc='Mailpile')
        if _p('mailpilev1', os.path.join(mailpilev1, 'mail'), must_exist=True):
            parent = os.path.dirname(mailpilev1)
            for profile in os.listdir(parent):
                if profile not in ('.', '..'):
                    profile_mail = os.path.join(parent, profile, 'mail')
                    _p('mailpilev1', profile_mail, must_exist=True)

    # How about Thunderbird?
    if thunderbird:
        from .importers.thunderbird import ThunderbirdConfig
        tbird = ThunderbirdConfig().load()
        for path in tbird.mailbox_paths():
            _p('thunderbird', path, must_exist=True)
        for path in tbird.imap_paths():
            _p('thunderbird', path)

    # FIXME: Look around for other mail clients and check their configs too

    return paths


if __name__ == '__main__':
    print('\n'.join('%s' % p for p in mail_path_suggestions(local=True)))
