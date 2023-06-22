# vim: set fileencoding=utf-8 :
#
# FIXME/TODO:
#   - The evaluation of Received headers is a bit crap, needs work.
#   - Is it worth creating a DNS RBL service for headerprints?
#
import re
import hashlib
import logging


# These are headers we just note the presence of, to differentiate between
# different MTAs. For the ones where we care about the contents, they are
# not listed here, but in the code below.
MTA_HP_HEADERS = ('return-path', 'errors-to', 'received-spf', 'list-owner',
                  'list-help', 'x-loop', 'x-no-archive', 'x-sequence',
                  'x-originating-ip',
                  'x-github-sender')

MTA_HP_REMAILED = ('resent-from', 'x-original-from', 'x-original-sender')

MUA_HP_HEADERS = ('date', 'from', 'to', 'reply-to',
                  # We omit the Subject, because for some reason it seems
                  # to jump around a lot. Same for CC.
                  'message-id', 'organization',
                  'mime-version', 'content-type',
                  # We leave out content-transfer-encoding, as it reflects
                  # the message content, not the tool used to create it.
                  'user-agent', 'x-mailer',
                  'x-mimeole', 'x-msmail-priority', 'x-priority',
                  'x-message-info',
                  'openpgp', 'x-openpgp',
                  # Common services
                  'x-github-recipient', 'feedback-id', 'x-facebook')

MUA_ID_HEADERS = ('x-mailer', 'user-agent', 'x-mimeole')

HP_MUA_ID_SPACE = re.compile(r'(\s+)')
HP_MUA_ID_IGNORE = re.compile(r'(\[[a-fA-F0-9%:]+\]|<\S+@\S+>'
                              '|(mail|in)-[^\.]+|\d+)')
HP_MUA_ID_SPLIT = re.compile(r'[\s,/;=()]+')
HP_RECVD_PARSE = re.compile(r'(by\s+)'
                             '[a-z0-9_\.-]*?([a-z0-9_-]+\.[a-z0-9_-]+\s+.*'
                             'with\s+E?SMTPS?).*$',
                            flags=(re.MULTILINE + re.DOTALL))

HP_DOM_AT_DOM = re.compile(r'^(\S+)@\1\.')


def _md5ish(data, length=32):
  try:
    data = data if isinstance(data, bytes) else bytes(data, 'utf-8')
    return hashlib.md5(data).hexdigest()[:length]
  except Exception as e:
    logging.debug('_md5ish(%s): %s' % (data, e))
    raise


def HeaderPrintMTADetails(parsed_message):
    """
    Extract details about the sender's outgoing SMTP server or
    mailing list manager.
    """
    details = []
    from_address = parsed_message.get('from', {}).get('address', '')
    from_domain = from_address.split('@')[-1]

    # We prefer mailing list identifiers or DKIM headers, as they are
    # explicitly trying to tell us which org this is.
    done = set()
    for h in MTA_HP_HEADERS:
        if h in parsed_message:
            details.append(h)
            continue

        if h in done:
            continue
        done.add(h)

    for h in ('list-id', 'list-unsubscribe', 'list-subscribe'):
        if h in parsed_message:
            val = parsed_message.get(h)[0].strip()
            val = val.replace(', ', ' ').split(' ')[0].strip()
            if val[:1] == '<':
                val = val[1:-1]
            val = '.'.join(val.split('?')[0].split('.')[-2:])
            details.extend([h, val])

    for h in (
            'dkim-signature', 'domainkey-signature',
            'x-google-dkim-signature'):
        if h in parsed_message:
            for dkim in (parsed_message.get(h) or []):
                attrs = ['%s=%s' % (k, v)
                    for k, v in dkim.items() if k[:1] in 'vacd']
                details.extend([h, '; '.join(sorted(attrs))])

    # Is this a remailing that replaces headers with X-Originals ?
    # If so, we make a note and skip some other checks.
    for h in MTA_HP_REMAILED:
        if h in parsed_message:
            details.append('remailed')
            break

    if 'remailed' not in details:
        # Is the org using its own mail servers? That's noteworthy.
        if from_domain:
            for rcvd in parsed_message.get('received') or []:
                if from_domain in rcvd.get('for', ''):
                    details.append('from-domain-in-received')
                    break

        # How many servers touched this mail before it got to us?
        details.append('hops:%d' % len(parsed_message.get('received', [])))

    if ('list-id' in details) or ('dkim-signature' in details):
        return details

    # If explicit organizational data is not found, analyze Received lines.

    # We want the first "non-local" received line. This can of course be
    # trivially spoofed, but looking at this will still protect against
    # all but the most targeted of spear phishing attacks.
    for rcvd in reversed(parsed_message.get('received') or []):
        if ('local' not in rcvd
                and ' mapi id ' not in rcvd
                and '127.0.0' not in rcvd
                and '[::1]' not in rcvd):
            if 'SMTP' in rcvd.get('with', ''):
                by = rcvd.get('by', '')
                by = HP_MUA_ID_SPACE.sub(' ', HP_MUA_ID_IGNORE.sub('x', by))
                details.append('Received ' + by)
                break

    return details


def HeaderPrintMUADetails(message, mta=None):
    """Summarize what the message tells us directly about the MUA."""
    details = []
    for lim, header_list in ((5, MUA_ID_HEADERS),):
        for header in header_list:
            values = message.get(header) or []
            if not isinstance(values, list):
                values = [values]
            for value in values:
                # We want some details about the MUA, but also some stability.
                # Thus the HP_MUA_ID_IGNORE regexp...
                value = ' '.join([
                    v for v in HP_MUA_ID_SPLIT.split(value.strip())
                    if not HP_MUA_ID_IGNORE.search(v)][:lim])
                details.extend([header, value.strip()])

    if not details:
        from_address = message.get('from', {}).get('address', '')
        from_userpart = from_address.split('@')[0].lower()
        # FIXME: We could definitely make more educated guesses!
        if 'x-cron-env' in message and 'Cron' in message.get('subject', ''):
            details.extend(['Guessed', 'CROND'])
        elif ('x-ms-tnef-correlator' in message or
                'x-ms-has-attach' in message):
            details.extend(['Guessed', 'Exchange'])
        elif '@mailpile' in message.get('message-id', ''):
            details.extend(['Guessed', 'Mailpile'])
        elif from_userpart in ('noreply', 'no-reply'):
            details.extend(['Guessed', 'No-Reply-Bot'])
        elif HP_DOM_AT_DOM.match(from_address) or from_userpart in ('info',):
            details.extend(['Guessed', 'Domain-Contact-Bot'])
        elif from_userpart in ('postmaster', 'mailer-daemon'):
            details.extend(['Guessed', 'Mailer-Daemon'])
        elif 'x-google-smtp-source' in message:
            details.extend(['Guessed', 'GMail'])

    return details


def HeaderPrintGenericDetails(parsed_message, which=MUA_HP_HEADERS):
    """Extract message details which may help identify the MUA."""
    return [k for k in parsed_message['_ORDER'] if k.lower() in which]


def HeaderPrints(parsed_message):
    """Generate fingerprints from message headers which identifies the MUA."""
    m = HeaderPrintMTADetails(parsed_message)
    u = HeaderPrintMUADetails(parsed_message, mta=m)[:20]
    g = HeaderPrintGenericDetails(parsed_message)[:50]

    sender = (
            parsed_message.get('reply-to') or
            parsed_message.get('x-original-from') or
            parsed_message.get('from', {})
        ).get('address', '-')

    mua = (u[1] if u else None)
    if mua and mua.startswith('Mozilla '):
        mua = mua.split()[-1]

    return {
        'org': _md5ish('\n'.join(m)),
        # The sender-ID headerprints includes MTA and sending org info
        'sender': _md5ish('\n'.join([sender]+m+u+g)),
        # Tool-chain headerprints ignore the MTA/org details
        'tools': _md5ish('\n'.join(u+g)),
        # The e-mail of the "most relevant" sender
        'email': sender,
        # Our best guess about what the MUA actually is; may be None
        'mua': mua}


if __name__ == "__main__":
    from ..email.parsemime import parse_message
    import doctest
    import json
    import sys
    results = doctest.testmod(optionflags=doctest.ELLIPSIS,
                              extraglobs={})
    if results.failed:
        sys.exit(1)

    if len(sys.argv) > 1:
        dump = True
        if sys.argv[1] == '-':
            test_msg = bytes(sys.stdin.read(64000), 'latin-1')
        else:
            test_msg = open(sys.argv[1], 'rb').read(64000)
    else:
        dump = False
        test_msg = b"""\
Received: from v14a (localhost [127.0.0.1])
        by v14a (Postfix) with ESMTP id B900221E1B02
        for <bre@localhost>; Sat,  1 Jan 2022 00:49:37 +0000 (GMT)
Received: from localhost [127.0.0.1]
        by v14a with POP3 (fetchmail-1.2.3)
        for <bre@localhost> (single-drop); Sat, 01 Jan 2022 00:49:37 +0000 (GMT)
Received: from a94-118.smtp-out.us-east-2.example.org (a94-118.smtp-out.us-east-2.example.org [1.2.3.4])
        by example.org (8.12.8/8.12.8) with ESMTP id 2010lvJe023743
        for <bre@example.org>; Sat, 1 Jan 2022 00:47:58 GMT
DKIM-Signature: v=1; a=rsa-sha256; q=dns/txt; c=relaxed/simple;
        s=ipiigoa5nqeewv73mvpezdefcnpkwwuq; d=example.org;
        t=1640998075;
        h=From:To:Subject:Date:Message-ID:MIME-Version:Content-Type;
        bh=j3MU3uZe2Q6nLs5CroCmMy8ab04OoO94IEJgjsmDu8E=;
        b=bWngIIMX55OtsFvA0OiZJc7UAPc95Se6jDN2LPa8QmflBFeFsFnX5Gp7XnfDjR2K
        zm83GE/edL6KcSrwWH+5PtAZcFFQW/Cl+0BB/xfuLEMdoSgNatMGa+Fokj1WOcwhl9k
        uc3BCOEk6fWiN70aJUdfFRk7amjWmXpM5ZCnV8Vo=
DKIM-Signature: v=1; a=rsa-sha256; q=dns/txt; c=relaxed/simple;
        s=xplzuhjr4seloozmmorg6obznvt7ijlt; d=example.org; t=1640998075;
        h=From:To:Subject:Date:Message-ID:MIME-Version:Content-Type:Feedback-ID;
        bh=j3MU3uZe2Q6nLs5CroCmMy8ab04OoO94IEJgjsmDu8E=;
        b=IjXobzFM6BI0E2yZp66/bD6EbJOkaSPmRYjmBSauMTVWRjbQIUnt+5mQgRsAH
        quroA88Vu0ZksCllf32Nn9WaZZtkc3uUFWaeJmeVylnEJyXwBzlhhgj3wpC2RhiNMF
        T45yNa35l2r8SDU6lzEvSvQ4xdrJ+OCfGqFIhWJes=
Subject: Ohai this is greats
From: bre@example.org
Message-ID: <testing@mailpile>
To: joe@example.org

This is a great test oh yes it is!
"""
    test_msg = parse_message(test_msg).with_text()

    m = HeaderPrintMTADetails(test_msg)
    u = HeaderPrintMUADetails(test_msg, mta=m)[:20]
    g = HeaderPrintGenericDetails(test_msg)[:50]
    if dump:
        print('Message:\n\t%s' % '\n\t'.join('%s: %s' % (k, v)
            for k, v in test_msg.items()
            if k[:1] != '_'))
        print('MTA info: %s' % m)
        print('MUA info: %s' % u)
        print('Generics: %s' % g)
        print('Headerprints: %s' % json.dumps(HeaderPrints(test_msg), indent=2))
    else:
        print('%s' % m)
        assert(len(m) == 6)
        assert('example.org' in ' '.join(m))
        assert('from-domain-in-received' in m)
        assert('Guessed' in u)
        assert('Mailpile' in u)
        assert(g == ['from', 'message-id', 'to'])
        print('%s' % (results, ))
