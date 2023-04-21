# Which keystores do we actually support?

## These are the default without networking enabled
DEFAULT_LOCAL_KEYSTORES = 'GnuPG:shared, autocrypt, email'

## These are the defaults with networking
DEFAULT_KEYSTORES = 'GnuPG:shared, autocrypt, email, WKD, KOO'


##[ Stubs and registry for lazy-loading ]####################################

def _DemoKeyStore(*args, **kwargs):
    from .demo import DemoKeyStore
    return DemoKeyStore(*args, **kwargs)


def _GnuPGKeyStore(*args, **kwargs):
    from .gnupg import GnuPGKeyStore
    return GnuPGKeyStore(*args, **kwargs)


def _EmailSearchKeyStore(*args, **kwargs):
    from .email_search import EmailSearchKeyStore
    return EmailSearchKeyStore(*args, **kwargs)


def _AutocryptKeyStore(*args, **kwargs):
    from .autocrypt import AutocryptKeyStore
    return AutocryptKeyStore(*args, **kwargs)


def _KooKeyStore(*args, **kwargs):
    from .koo import KooKeyStore
    return KooKeyStore(*args, **kwargs)


def _WKDKeyStore(*args, **kwargs):
    from .wkd import WKDKeyStore
    return WKDKeyStore(*args, **kwargs)


KEYSTORE_REGISTRY = {
    'demo': _DemoKeyStore,
    'autocrypt': _AutocryptKeyStore,
    'gnupg': _GnuPGKeyStore,
    'email': _EmailSearchKeyStore,
    'koo':   _KooKeyStore,
    'wkd':   _WKDKeyStore}

# FIXME:
#   - It would be nice to be able to parse Thunderbird's keychain?
#   - Use the search engine to find keys in mail
#   - Autocrypt!
