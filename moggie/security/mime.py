import re

_MSM_FIX_MIMETYPES = {
    'image/jpg': 'image/jpeg',
    'multipart/alternative': 'text/plain',
    'multipart/related': 'text/plain',
    'multipart/mixed': 'text/plain'}
_MSM_RISKY_FN_CHARS = re.compile(r'[^a-zA-Z0-9_\.-]')
_MSM_EVIL_EXTENSIONS = set(['exe', 'dll', 'scr', 'com'])  # FIXME
_MSM_RISKY_MT_CHARS = re.compile(r'[^a-z0-9_\./-]')


def part_filename(part, unsafe=False):
    filename = ''
    disp = part.get('content-disposition')
    ctype = part.get('content-type')
    for which, attr in ((disp, 'filename'), (ctype, 'name')):
        if which and attr in which[1]:
            if unsafe:
                filename = which[1][attr]
            else:
                filename = _MSM_RISKY_FN_CHARS.sub('_', which[1][attr])
            if filename:
                return filename
    return None


def magic_part_id(idx, part, unsafe=False):
    mimetype = part.get('content-type', ['application/octet-stream'])[0]
    if not unsafe:
        mimetype = _MSM_RISKY_MT_CHARS.sub('_', mimetype.lower())
    mimetype = _MSM_FIX_MIMETYPES.get(mimetype, mimetype)

    filename = part_filename(part, unsafe=unsafe) or ''
    if filename:
        ext = filename.split('.')[-1]
        # FIXME: Fix mime-type based on extension? Check for mismatch?
        if ('..' in filename) or (ext in _MSM_EVIL_EXTENSIONS):
            return None

    return 'part-%d-%s%s%s' % (
        idx, mimetype.replace('/', '-'), '/' if filename else '', filename)
