def _lazy_admin(cls):
    import moggie.app.cli.admin as mod
    return getattr(mod, cls)

def _lazy_annotate(cls):
    import moggie.app.cli.annotate as mod
    return getattr(mod, cls)

def _lazy_autotag(cls):
    import moggie.app.cli.autotag as mod
    return getattr(mod, cls)

def _lazy_dev(cls):
    import moggie.app.cli.dev as mod
    return getattr(mod, cls)

def _lazy_email(cls):
    import moggie.app.cli.email as mod
    return getattr(mod, cls)

def _lazy_help(cls):
    import moggie.app.cli.help as mod
    return getattr(mod, cls)

def _lazy_mailboxes(cls):
    import moggie.app.cli.mailboxes as mod
    return getattr(mod, cls)

def _lazy_notmuch(cls):
    import moggie.app.cli.notmuch as mod
    return getattr(mod, cls)

def _lazy_plan(cls):
    import moggie.app.cli.plan as mod
    return getattr(mod, cls)

def _lazy_openpgp(cls):
    import moggie.app.cli.openpgp as mod
    return getattr(mod, cls)

def _lazy_sendmail(cls):
    import moggie.app.cli.sendmail as mod
    return getattr(mod, cls)


class LazyLoader(dict):
    @classmethod
    def LoadAll(cls):
        import moggie.app.cli.admin
        import moggie.app.cli.autotag
        import moggie.app.cli.help
        import moggie.app.cli.email
        import moggie.app.cli.notmuch
        import moggie.app.cli.openpgp

    def get(self, name):
        val = super().get(name)
        if isinstance(val, tuple):
            loader, name = val
            val = loader(name)
        return val


CLI_COMMANDS = LazyLoader({
    'help': (_lazy_help, 'CommandHelp'),

    'address': (_lazy_notmuch, 'CommandAddress'),
    'count':   (_lazy_notmuch, 'CommandCount'),
    'search':  (_lazy_notmuch, 'CommandSearch'),
    'show':    (_lazy_notmuch, 'CommandShow'),
    'reply':   (_lazy_notmuch, 'CommandReply'),
    'tag':     (_lazy_notmuch, 'CommandTag'),

    'email': (_lazy_email, 'CommandEmail'),
    'parse': (_lazy_email, 'CommandParse'),

    'annotate': (_lazy_annotate, 'CommandAnnotate'),
    'send': (_lazy_sendmail, 'CommandSend'),

    'copy': (_lazy_mailboxes, 'CommandCopy'),
    'move': (_lazy_mailboxes, 'CommandMove'),
#   'remove': (_lazy_mailboxes, 'CommandRemove'),
#   'sync': (_lazy_mailboxes, 'CommandSync'),

    'welcome': (_lazy_admin, 'CommandWelcome'),
    'unlock':  (_lazy_admin, 'CommandUnlock'),
    'context': (_lazy_admin, 'CommandContext'),
    'grant':   (_lazy_admin, 'CommandGrant'),
    'import':  (_lazy_admin, 'CommandImport'),
    'new':     (_lazy_admin, 'CommandNew'),
    'browse':  (_lazy_admin, 'CommandBrowse'),
    'encrypt': (_lazy_admin, 'CommandEnableEncryption'),

    'plan':    (_lazy_plan, 'CommandPlan'),

    'autotag': (_lazy_autotag, 'CommandAutotag'),
    'autotag-train': (_lazy_autotag, 'CommandAutotagTrain'),
    'autotag-classify': (_lazy_autotag, 'CommandAutotagClassify'),

    'websocket': (_lazy_dev, 'CommandWebsocket'),
    'notifications': (_lazy_dev, 'CommandNotifications'),

    'pgp-get-keys': (_lazy_openpgp, 'CommandPGPGetKeys'),
    'pgp-add-keys': (_lazy_openpgp, 'CommandPGPAddKeys'),
    'pgp-del-keys': (_lazy_openpgp, 'CommandPGPDelKeys'),
    'pgp-sign':     (_lazy_openpgp, 'CommandPGPSign'),
    'pgp-encrypt':  (_lazy_openpgp, 'CommandPGPEncrypt'),
    'pgp-decrypt':  (_lazy_openpgp, 'CommandPGPDecrypt'),
    'pgp-verify':   (_lazy_openpgp, 'CommandPGPVerify'),
    })
