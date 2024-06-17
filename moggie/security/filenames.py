import re

RE_FN_DISALLOWED = re.compile(r'([/\\:]|\.\.)')
RE_FN_RISKY_EXT = re.compile(r'\.(exe|dll|com)$')  # FIXME: Add more?


def clean_filename(fn):
    """
    Replace potentially risky substrings in filenames with underscores.

    >>> clean_filename('this\\is/..:evil.txt')
    'this_is___evil.txt'

    >>> clean_filename('trojan.exe')
    'trojan_exe.dat'

    >>> clean_filename('trojan.dll')
    'trojan_dll.dat'

    >>> clean_filename('trojan.com')
    'trojan_com.dat'
    """
    return RE_FN_RISKY_EXT.sub('_\\1.dat', RE_FN_DISALLOWED.sub('_', fn))
