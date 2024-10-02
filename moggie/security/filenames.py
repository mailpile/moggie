import re

RE_FN_DISALLOWED = re.compile(r'([/\\:\s]|\.\.)')
RE_FN_RISKY_EXT = re.compile(r'\.(exe|dll)$')  # FIXME: Add more?


def clean_filename(fn):
    """
    Replace potentially risky substrings in filenames with underscores.

    >>> clean_filename('this\\\\is/..:evil.txt')
    'this_is___evil.txt'

    >>> clean_filename('trojan.dll')
    'trojan_dll.dat'

    >>> clean_filename('domain.com')
    'domain.com'

    >>> clean_filename('evil trojan horse.exe')
    'evil_trojan_horse_exe.dat'
    """
    return RE_FN_RISKY_EXT.sub('_\\1.dat', RE_FN_DISALLOWED.sub('_', fn))
