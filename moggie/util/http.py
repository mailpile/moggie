import json
import socket


def url_parts(url):
    parts = url.split('/', 3)
    prot = parts[0].rstrip(':')
    hopo = parts[2].split(':')
    path = parts[3] if (len(parts) == 4) else ''
    host = hopo[0]
    port = int(hopo[1] if (len(hopo) > 1)
        else (443 if (prot == 'https') else 80))
    return (prot, host, port, '/'+path)


# This is deliberately minimal, to keep the overhead of our
# localhost RPC stuff as low as possible.
#
def http1x_connect(host, port, path,
        method='GET', ver='1.0', timeout=60, more=False, headers=''):

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(max(1, timeout//30))
    sock.connect((host, int(port)))
    sock.settimeout(timeout)

    if not headers or 'Host:' not in headers:
        headers = 'Host: %s\r\n%s' % (host, headers)

    if not more and 'Content-Length:' not in headers:
        headers += 'Content-Length: 0\r\n'

    sock.send(('%s %s HTTP/%s\r\n%s\r\n' % (method, '/' + path.lstrip('/'), ver, headers)
        ).encode('latin-1'))

    if not more:
        sock.shutdown(socket.SHUT_WR)
    return sock


def http1x_jsonup(host, port, path, data, **conn_kwargs):
    data = json.dump(data)
    conn_kwargs.update({
        'method': 'POST',
        'more': True,
        'ver': ver,
        'headers': (
            ('Content-Type: application/json\r\n') +
            ('Content-Length: %d\r\n' % len(data)))})
    sock = None
    try:
        sock = http1x_connect(host, port, path, **conn_kwargs)
        for ofs in range(0, len(data), 4096):
            sock.send(data[ofs:ofs+4096])
        sock.shutdown(socket.SHUT_WR)
        return sock.read()
    finally:
        if sock:
            sock.close()
