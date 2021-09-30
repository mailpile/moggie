import binascii
import os
from .metadata import Metadata

# Need to think about this a bit more...
#
# We need to have a way to create "templates", which are a combination of
# composer settings, message features and suggested content.
#
# Then we need to capture user input and store in-progress message drafts,
# so we can recreate composer state and generate an actual message to send.
#
# In order to support mutt-like editing, we need a way to generate and parse
# message headers that configure Moggie behaviors as well.
#
# If we build on Metadata() we can store drafts directly in the index. So
# that's nice. Then when a message is finalized, we can generate an actual
# message which is a snapshot of settings and state.
#
# We should probably have a default Template for any given account. With a
# way to create and use others. Others could be per-profile?
#

DEFAULT_TEMPLATE = """\
Message-Id: %(message_id)s
Date: %(date)s
From: %(from)s
To: %(to)s
Cc: %(cc)s
Bcc: %(bcc)s
Subject: %(subject)s
Attach: %(attachments)s
Features: %(features)s

"""


def make_message_id():
    rval = str(
            binascii.b2a_base64(os.urandom(32), newline=False),
            'latin-1'
        ).replace('=', '').replace('/', '_')
    return ('<%s@moggie>' % (rval))


class MessageDraft(Metadata):
    SINGLE_CLI_ARGS = ()
    ALL_CLI_ARGS = ('-a', '-b', '-c', '-H', '-i', '-s')

    def __init__(self, headers=None, more=None):
        # FIXME: We always want to know which profile a draft is associated with.
        super().__init__(0, 'DRAFT', 0, 0, 0,
            headers or DEFAULT_TEMPLATE,
            more=more)

    @classmethod
    def FromMetadata(cls, metadata):
        return cls(headers=metadata.headers, more=metadata.more)

    @classmethod
    def FromArgs(cls, args):
        m = {}
        while args:
            arg = args.pop(0)
            # FIXME: Allow user to specify from-address somehow!
            if arg == '-a':
                m['attach'] = m.get('attach', []) + [args.pop(0)]
            elif arg == '-b':
                m['bcc'] = m.get('bcc', []) + [args.pop(0)]
            elif arg == '-c':
                m['cc'] = m.get('cc', []) + [args.pop(0)]
            elif arg == '-H':
                raise Exception('FIXME: Implement -H')
            elif arg == '-i':
                raise Exception('FIXME: Implement -i')
            elif arg == '-s':
                m['subject'] = (m.get('subject', '') + ' ' + args.pop(0)).strip()
            elif arg[:1] != '-':
                m['to'] = m.get('to', []) + [arg]
            else:
                raise Exception('Unhandled arg: %s' % arg)
        return cls(more=m)

    @classmethod
    def FromEditable(cls, args):
        pass

    def default_features(self):
        # FIXME
        return ['postpone:2m', 'inline-quote', 'reflow']

    def no_subject(self):
        # FIXME
        return '(no subject)'

    def generate_editable(self):
        # FIXME: Bodies, Attachments, MIME, PGP... so much fun!
        m = self.more.get
        headers = self.headers % {
            'message_id': m('message_id') or make_message_id(),
            'date': m('date', 'now'),
            'from': ' '.join(m('from', [])),
            'to': ' '.join(m('to', [])),
            'cc': ' '.join(m('cc', [])),
            'bcc': ' '.join(m('bcc', [])),
            'subject': m('subject') or self.no_subject(),
            'attachments': ' '.join(m('attach', [])),
            'features': ', '.join(m('features', self.default_features()))}
        return '%s\n\n%s' % (headers, self.more.get('body', '(no message)'))


def FakeDraftMain(sys_args, args):
    tpl = MessageDraft.FromArgs(args)
    print('%s' % (tpl.generate_editable(),))


if __name__ == "__main__":
    import sys
    FakeMain(sys.argv[1:])
