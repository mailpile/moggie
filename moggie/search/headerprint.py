# vim: set fileencoding=utf-8 :
#
import re
import hashlib


MUA_ML_HEADERS = (# Mailing lists are sending MUAs in their own right
                  'list-id', 'list-subscribe', 'list-unsubscribe')

MUA_HP_HEADERS = ('date', 'from', 'to', 'reply-to',
                  # We omit the Subject, because for some reason it seems
                  # to jump around a lot. Same for CC.
                  'message-id', 'return-path', 'precedence', 'organization',
                  'mime-version', 'content-type',
                  'user-agent', 'x-mailer',
                  'x-mimeole', 'x-msmail-priority', 'x-priority',
                  'x-originating-ip', 'x-message-info',
                  'openpgp', 'x-openpgp',
                  # Common services
                  'x-github-recipient', 'feedback-id', 'x-facebook')

MUA_ID_HEADERS = ('x-mailer', 'user-agent', 'x-mimeole')

HP_MUA_ID_SPACE = re.compile(r'(\s+)')
HP_MUA_ID_IGNORE = re.compile(r'(\[[a-fA-F0-9%:]+\]|<\S+@\S+>'
                              '|(mail|in)-[^\.]+|\d+)')
HP_MUA_ID_SPLIT = re.compile(r'[\s,/;=()]+')
HP_RECVD_PARSE = re.compile(r'(by\s+)'
                             '[a-z0-9_\.-]*?([a-z0-9_-]*?\.?[a-z0-9_-]+\s+.*'
                             'with\s+.*)\s+id\s+.*$',
                            flags=(re.MULTILINE + re.DOTALL))


def _md5ish(data, length=12):
    data = data if isinstance(data, bytes) else bytes(data, 'latin-1')
    return hashlib.md5(data).hexdigest()[:length]


def HeaderPrintMTADetails(parsed_message):
    """Extract details about the sender's outgoing SMTP server."""
    details = []
    # We want the first "non-local" received line. This can of course be
    # trivially spoofed, but looking at this will still protect against
    # all but the most targeted of spear phishing attacks.
    for rcvd in reversed(parsed_message.get('received') or []):
        if ('local' not in rcvd
                and ' mapi id ' not in rcvd
                and '127.0.0' not in rcvd
                and '[::1]' not in rcvd):
            parsed = HP_RECVD_PARSE.search(rcvd)
            if parsed:
                by = parsed.group(1) + parsed.group(2)
                by = HP_MUA_ID_SPACE.sub(' ', HP_MUA_ID_IGNORE.sub('x', by))
                details = ['Received ' + by]
                break
    for h in ('DKIM-Signature', 'X-Google-DKIM-Signature'):
        for dkim in (parsed_message.get(h) or []):
            attrs = [HP_MUA_ID_SPACE.sub('', a)
                     for a in dkim.split(';') if a.strip()[:1] in 'vacd']
            details.extend([h, '; '.join(sorted(attrs))])
    return details


def HeaderPrintMUADetails(message, mta=None):
    """Summarize what the message tells us directly about the MUA."""
    details = []
    for header in MUA_ID_HEADERS:
        values = message.get(header) or []
        if not isinstance(values, list):
            values = [values]
        for value in values:
            # We want some details about the MUA, but also some stability.
            # Thus the HP_MUA_ID_IGNORE regexp...
            value = ' '.join([v for v in HP_MUA_ID_SPLIT.split(value.strip())
                              if not HP_MUA_ID_IGNORE.search(v)])
            details.extend([header, value.strip()])

    if not details:
        # FIXME: We could definitely make more educated guesses!
        if mta and mta[0].startswith('Received by google.com'):
            details.extend(['Guessed', 'GMail'])
        elif ('x-ms-tnef-correlator' in message or
                'x-ms-has-attach' in message):
            details.extend(['Guessed', 'Exchange'])
        elif '@mailpile' in message.get('message-id', ''):
            details.extend(['Guessed', 'Mailpile'])

    return details


def HeaderPrintGenericDetails(parsed_message, which=MUA_HP_HEADERS):
    """Extract message details which may help identify the MUA."""
    return [k for k in parsed_message['_ORDER'] if k.lower() in which]


def HeaderPrints(parsed_message):
    """Generate fingerprints from message headers which identifies the MUA."""
    m = HeaderPrintMTADetails(parsed_message)
    u = HeaderPrintMUADetails(parsed_message, mta=m)[:20]
    g = HeaderPrintGenericDetails(parsed_message)[:50]
    mua = (u[1] if u else None)
    if mua and mua.startswith('Mozilla '):
        mua = mua.split()[-1]
    return {
        # The sender-ID headerprints includes MTA info
        'sender': _md5ish('\n'.join(m+u+g)),
        # Tool-chain headerprints ignore the MTA details
        'tools': _md5ish('\n'.join(u+g)),
        # Our best guess about what the MUA actually is; may be None
        'mua': mua}


if __name__ == "__main__":
    from ..email.parsemime import parse_message
    import doctest
    import sys
    results = doctest.testmod(optionflags=doctest.ELLIPSIS,
                              extraglobs={})
    if results.failed:
        sys.exit(1)

    if len(sys.argv) > 1:
        dump = True
        test_msg = open(sys.argv[1], 'rb').read(10240)
    else:
        dump = False
        test_msg = b"""\
Received: from v14a (localhost [127.0.0.1])
        by v14a (Postfix) with ESMTP id B900221E1B02
        for <bre@localhost>; Sat,  1 Jan 2022 00:49:37 +0000 (GMT)
Received: from localhost [127.0.0.1]
        by v14a with POP3 (fetchmail-6.4.0.rc4)
        for <bre@localhost> (single-drop); Sat, 01 Jan 2022 00:49:37 +0000 (GMT)
Received: from a94-118.smtp-out.us-east-2.amazonses.com (a94-118.smtp-out.us-east-2.amazonses.com [54.240.94.118])
        by example.org (8.12.8/8.12.8) with ESMTP id 2010lvJe023743
        for <bre@example.org>; Sat, 1 Jan 2022 00:47:58 GMT
DKIM-Signature: v=1; a=rsa-sha256; q=dns/txt; c=relaxed/simple;
        s=ipiigoa5nqeewv73mvpezdefcnpkwwuq; d=netflyleads.com;
        t=1640998075;
        h=From:To:Subject:Date:Message-ID:MIME-Version:Content-Type;
        bh=j3MU3uZe2Q6nLs5CroCmMy8ab04OoO94IEJgjsmDu8E=;
        b=bWngIIMX55OtsFvA0OiZJc7UAPc95Se6jDN2LPa8QmflBFeFsFnX5Gp7XnfDjR2K
        zm83GE/edL6KcSrwWH+5PtAZcFFQW/Cl+0BB/xfuLEMdoSgNatMGa+Fokj1WOcwhl9k
        uc3BCOEk6fWiN70aJUdfFRk7amjWmXpM5ZCnV8Vo=
DKIM-Signature: v=1; a=rsa-sha256; q=dns/txt; c=relaxed/simple;
        s=xplzuhjr4seloozmmorg6obznvt7ijlt; d=amazonses.com; t=1640998075;
        h=From:To:Subject:Date:Message-ID:MIME-Version:Content-Type:Feedback-ID;
        bh=j3MU3uZe2Q6nLs5CroCmMy8ab04OoO94IEJgjsmDu8E=;
        b=IjXobzFM6BI0E2yZp66/bD6EbJOkaSPmRYjmBSauMTVWRjbQIUnt+5mQgRsAH
        quroA88Vu0ZksCllf32Nn9WaZZtkc3uUFWaeJmeVylnEJyXwBzlhhgj3wpC2RhiNMF
        T45yNa35l2r8SDU6lzEvSvQ4xdrJ+OCfGqFIhWJes=
Subject: Ohai this is greats
From: bre@example.org
Message-Id: <testing@mailpile>
To: joe@example.org

This is a great test oh yes it is!
"""
    test_msg = parse_message(test_msg).with_text()

    m = HeaderPrintMTADetails(test_msg)
    u = HeaderPrintMUADetails(test_msg, mta=m)[:20]
    g = HeaderPrintGenericDetails(test_msg)[:50]
    if dump:
        print('Message: %s' % test_msg)
        print('MTA info: %s' % m)
        print('MUA info: %s' % u)
        print('Generics: %s' % g)
    else:
        assert(len(m) == 1)
        assert('example.org' in m[0])
        assert('Guessed' in u)
        assert('Mailpile' in u)
        assert(g == ['from', 'message-id', 'to'])
        print('%s' % (results, ))
