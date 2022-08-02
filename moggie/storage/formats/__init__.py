def split_tagged_path(path):
    """
    A tagged path allows us to reference subresources within a file.
    Example:

       b'/path/to/file/4,2[csv:4]'

    The 'csv' is the type of the sub-path, and '4' is the hexadecimal
    length of the appended sub-path. This parses to:

       [(None, b'/path/to/file'), (b'csv', b'/4,2')]

    If the original path ends with a ']' character, it would be encoded
    like so (null type, zero-length subpath):

       b'/path/to/file[1][:0]'
    """
    paths = []
    while path.endswith(b']'):
        path, pathtag = path[:-1].rsplit(b'[', 1)
        path_type, path_len = pathtag.split(b':')
        path_len = int(path_len, 16)
        if path_len:
            path, subpath = path[:-path_len], path[-path_len:]
            paths.append((path_type, subpath))
        else:
            break
    paths.append(path)
    return list(reversed(paths))


def tag_path(filepath, *subpaths):
    if filepath.endswith(b']'):
        filepath += b'[:0]'
    for subtype, subpath in subpaths:
       filepath += (subpath +b'['+ subtype + (b':%x]' % len(subpath)))
    return filepath



