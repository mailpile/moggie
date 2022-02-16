"""
This server will assist with a privacy-preserving password recovery
flow, suitable for use with encryption keys of moderate importance.

## User Interface Recommendations

From the point of view of the user, this scheme requires the following
user interactions:

### Setup

   * The app requests permission to allow password resets (key recovery)
      1. The user provides e-mail addresses and/or cell phone numbers
      2. The user chooses a reset policy (require all, accept any, N of M)

### Recovery

   1. The user triggers a "password reset"
      * The user is informed that codes have been sent via e-mail and/or SMS
   2. The user inputs all recieved codes into the app
   3. The user chooses a new password

### Renewal

   1. About 1x per year, the user should be reminded they have recovery
      enabled, and asked whether they want to make any changes; if they
      choose to make changes, this becomes the Setup interaction.


If the app developer chooses to keep things as simple as possible, they can
decide to only prompt for a single recovery method (e-mail or SMS), or decide
on the user's behalf what policy to use.

This is deliberately very similar to the "password reset" flow provided
by popular "software as a service" systems.


## How Does It Work?

...



## Security vs. Reliability


## API Documentation

------

Service at %(servername)s?

   POST /recovery_svc/register
       <- (hint, passcode, e-mails or phone numbers)
       -> (reset-code, id, expiration-date)

   POST /recovery_svc/renew
       <- (id, old-expiration-date)
       -> (id, new-expiration-date)

   POST /recovery_svc/recover
       <- (reset-code, id)
       -> ('ok, message(s) sent') or 400
     -email-> (temporary-code, instuctions)
     --SMS--> (temporary-code, instuctions)

   POST /recovery_svc/code
       <- (reset-code, id, temporary-code)
       -> (passcode) or 400

The passcode must be a string of decimal numbers and dashes. The server
will also strictly limit the volume of data it will accept.

Internally, the scheme sends one of two passcodes (passcode-b) to the
server, where  passcode-a and passcode-b are both strings of decimal
numbers and dashes, like so:

   passcode-a: 1111-22-3456
   passcode-b: 4444-11-5555

Both passcodes contain the same number of digits, which are then
combined using per-digit addition (modulo 10) to generate an actual
encryption key, like so:

   passcode: 5555-33-8901

The recovery server never has this full passcode, it only stores the
passcode-b input and will only reveal that to users which can pass
a 2-factor authenticcation scheme (a shared secret and an e-mail or
SMS-based external verification). To safeguard the e-mail addresses at
the recovery service, the passcode and e-mail are stored encrypted, the
reset-code is the decryption key for that data.

This guarantees that neither the recovery service nor the user need keep
the actual passcode on file. The full recovery code should never persist
outside of RAM.
"""
import binascii
import os
import json
import sys
import random
import re
import socket
import time
import traceback

import markdown

from upagekite.httpd import url
from upagekite.web import process_post

from ..config import AppConfig
from ..crypto.aes_utils import make_aes_key
from ..crypto.recovery import generate_recovery_code, combine_recovery_codes
from ..crypto.recovery import VALID_CODE_RE, VALID_EMAIL_RE
from ..storage.records import RecordStore
from .public import PublicWorker, require


EXPIRATION_TIME = 2*365*24*3600  # 2 years
TEMP_CODE_TIME = 20 * 60

LAST_EXPIRATION = 0

WEB_ROOT = None


@url('/')
def web_root(req_env):
    global WEB_ROOT
    if WEB_ROOT is None:
        params = {
            'servername': req_env['worker'].kite_name}
        params.update({
            'docs': markdown.markdown(__doc__ % params)})
        WEB_ROOT = ("""\
<html><head>
  <title>Secret Recovery Service</title>
  <style type="text/css">
    body {background: #eef; color: #111;}
    .content {margin: 0 auto 1em auto; max-width: 600px;}
  </style>
</head><body><div class=content>
  <h1>Secret Recovery Service</h1>
  <p>Welcome to the Secret Recovery Service on <b>%(servername)s</b>.</p>
  <hr><h1>Technical Details</h1>
  %(docs)s
<div></body></html>
""") % params

    return {'mimetype': 'text/html', 'body': WEB_ROOT}


@url('/recovery_svc/register')
@process_post(max_bytes=2048)
def web_register(req_env):
    require(req_env, post=True, secure=True)

    global LAST_EXPIRATION
    posted = req_env.post_data
    print('%s '% posted)

    hint = posted.get('hint', '')
    passcode = posted.get('passcode', '')
    contacts = posted.get('contacts', [])
    if not (passcode
            and (0 < len(contacts) < 4)
            and contacts
            and re.match(VALID_CODE_RE, passcode)):
        return {'code': 400, 'msg': 'Bad request', 'body': 'Bad request'}
    for e in contacts:
        # FIXME: Also support SMS messages?
        if not re.match(VALID_EMAIL_RE, e):
            return {'code': 400, 'msg': 'Bad request', 'body': 'Bad request'}

    # FIXME: We might want a smarter expiration system than this!
    while True:
        expiration = int(time.time() + EXPIRATION_TIME)
        if expiration != LAST_EXPIRATION:
            break
        time.sleep(0.5)
    LAST_EXPIRATION = expiration

    secret = generate_recovery_code()
    aes_key = make_aes_key(secret.encode('latin-1'))
    exp_key = 'expire:%x' % expiration
    req_env['records'].set(exp_key, {
        'hint': hint,
        'passcode': passcode,
        'contacts': contacts}, encrypt=True, aes_key=aes_key)

    return {
        'mimetype': 'application/json',
        'body': json.dumps({
            'id': expiration,  # This changes if we fix the FIXME above
            'expires': expiration,
            'reset_code': secret})}


@url('/recovery_svc/recover')
@process_post(max_bytes=2048)
def web_recover(req_env):
    require(req_env, post=True, secure=True)
    posted = req_env.post_data

    _id = posted.get('id', 0)
    reset_code = posted.get('reset_code', '')
    if not (_id
            and reset_code
            and re.match(VALID_CODE_RE, reset_code)):
        return {'code': 400, 'msg': 'Bad request', 'body': 'Bad request'}

    exp_key = 'expire:%x' % (_id,)
    aes_key = make_aes_key(reset_code.encode('latin-1'))
    info = req_env['records'].get(exp_key, aes_key=aes_key)
    if info is None:
        return {'code': 400, 'msg': 'Bad request', 'body': 'Bad request'}

    temp_code = generate_recovery_code(1, 6)
    expires = int(time.time()) + TEMP_CODE_TIME
    req_env['codes'][temp_code] = expires

    print('FIXME: should send %s to %s' % (temp_code, info['contacts']))

    def _a(addr):
        if '@' in addr:
            p = addr.split('@', 1)
            d = p[1].rsplit('.', 1)
            dv = len(d[0]) // 3
            do = len(d[0]) - dv
            return '%s*@%s%s.%s' % (p[0][:2], d[0][:dv], ('*' * do), d[1])
        return addr
    return {
        'ttl': 30,
        'mimetype': 'application/json',
        'body': json.dumps({
            'expires': expires,
            'sent_to': [_a(i) for i in info['contacts']]})}


@url('/recovery_svc/code')
@process_post(max_bytes=2048)
def web_recover(req_env):
    require(req_env, post=True, secure=True)
    posted = req_env.post_data
    return {
        'ttl': 30,
        'mimetype': 'application/json',
        'body': '{"soon": true}'}


class RecoverySvcWorker(PublicWorker):
    """
    This is the main "public facing" app worker, it implements the main
    web API and application logic. It uses the upagekite event loop and
    HTTP daemon.
    """

    KIND = 'recovery_svc'
    NICE = 15  # Lower our priority

    CONFIG_SECTION = AppConfig.RECOVERY_SVC

    PUBLIC_PATHS = ['/']
    PUBLIC_PREFIXES = ['/recovery_svc']

    def __init__(self, *args, **kwargs):
        PublicWorker.__init__(self, *args, **kwargs)
        self.records = None

    def startup_tasks(self):
        self.records = RecordStore(
            os.path.join(self.profile_dir, self.KIND),
            self.KIND)
        self.shared_req_env['records'] = self.records
        self.shared_req_env['codes'] = {}


if __name__ == '__main__':
    import sys
    aw = RecoverySvcWorker.FromArgs('/tmp', sys.argv[1:])
    if aw.connect():
        try:
            print('** We are live, yay')
            #print(aw.capabilities())
            print('** Tests passed, waiting... **')
            aw.join()
        finally:
            aw.terminate()
