# Routines for reading Thunderbird's configuration file(s)
#
# When configuring new accounts, we can take a peek here to see if there
# are any clues we can use.
#
import os
import json


class ThunderbirdConfig:
    def __init__(self):
        self.loaded = 0
        self.settings = {}

    def load(self):
        tbird = os.path.expanduser('~/.thunderbird')
        if not os.path.exists(tbird):
            return self

        self.loaded = 0
        for profile in (os.path.join(tbird, d) for d in os.listdir(tbird)):
            try:
                with open(os.path.join(profile, 'prefs.js'), 'r') as prefs:
                    self.settings[profile] = self.parse(prefs.read())
                    self.loaded += 1
            except (OSError, IOError):
                pass

        return self

    def parse(self, data):
        prefs = {}

        def _parse_val(val):
            try:
                if val[:2] == '"{' and val[-2:] == '}"':
                    return json.loads(json.loads(val))
                return json.loads(val)
            except:
                return val

        for line in (l.strip() for l in data.splitlines()):
            if line.startswith('user_pref("') and line.endswith(');'):
                key, val = line[11:-2].split('", ', 1)
                prefs[key] = _parse_val(val)

        return prefs

    def mailbox_paths(self):
        for profile, prefs in self.settings.items():
            for pref, val in prefs.items():
                if pref.startswith('mail.root.') and pref[-4:] != '-rel':
                    yield val

    def imap_paths(self):
        discovered = set()
        for profile, prefs in self.settings.items():
            for pref, val in prefs.items():
                if isinstance(val, str) and val.startswith('imap://'):
                    imap = '/'.join(val.split('/')[:3])
                    if imap not in discovered:
                        discovered.add(imap)
                        yield imap


if __name__ == '__main__':
    print(json.dumps(ThunderbirdConfig().load().settings, indent=2))
