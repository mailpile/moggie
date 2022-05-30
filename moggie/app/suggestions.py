# FIXME: i18n i18n i18n ...


class Suggestion:
    MESSAGE = 'Override this message!'
    ID = None

    UI_QUIT = 'quit'
    UI_ACCOUNTS = 'accounts'
    UI_BROWSE = 'browse'
    UI_ENCRYPT = 'encrypt'

    UI_ACTION = None

    def __init__(self, context, config):
        self.context = context
        self.config = config

    @classmethod
    def If_Wanted(cls, context, config):
        return cls(context, config)

    def message(self):
        return self.MESSAGE

    def action(self):
        return self.UI_ACTION


class SuggestionWelcome(Suggestion):
    MESSAGE = 'Welcome to Moggie! Press `q` to quit.'
    UI_ACTION = Suggestion.UI_QUIT
    ID = 0


class SuggestionAddAccount(Suggestion):
    MESSAGE = 'Add one or more e-mail accounts'
    UI_ACTION = Suggestion.UI_ACCOUNTS
    ID = 1
    # FIXME: Make wanted() check whether accounts have been added.


class SuggestionBrowse(Suggestion):
    MESSAGE = 'Browse for mail on this computer'
    UI_ACTION = Suggestion.UI_BROWSE
    ID = 2 
    # FIXME: Make wanted() check whether mailboxes have been added.


class SuggestionEncrypt(Suggestion):
    MESSAGE = 'Lock the app and encrypt your local data'
    UI_ACTION = Suggestion.UI_ENCRYPT
    ID = 3

    @classmethod
    def If_Wanted(cls, context, config):
        if config and config.get(config.SECRETS, 'passphrase'):
            return None
        return cls(context, config)


SUGGESTIONS = dict((s.ID, s) for s in [
    SuggestionWelcome,
    SuggestionAddAccount,
    SuggestionBrowse,
    SuggestionEncrypt,
    ])
