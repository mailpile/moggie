import socket

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

    sock.send(('%s %s HTTP/%s\r\n%s\r\n' % (method, path, ver, headers)
        ).encode('latin-1'))

    if not more:
        sock.shutdown(socket.SHUT_WR)
    return sock

