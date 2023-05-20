# FIXME: i18n i18n i18n ...


class Suggestion:
    MESSAGE = 'Override this message!'
    ID = None

    UI_QUIT = 'quit'
    UI_HELP = 'help'
    UI_ACCOUNTS = 'accounts'
    UI_BROWSE = 'browse'
    UI_ENCRYPT = 'encrypt'

    UI_ACTION = None

    def __init__(self, context, config):
        self.context = context
        self.config = config

    @classmethod
    def If_Wanted(cls, context, config, **info):
        return cls(context, config)

    def message(self):
        return self.MESSAGE

    def action(self):
        return self.UI_ACTION


class SuggestionWelcome(Suggestion):
    MESSAGE = 'Use arrows/enter to move/select, `q` to quit!'
    UI_ACTION = Suggestion.UI_HELP
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
    def If_Wanted(cls, context, config, **info):
        # FIXME: The app does not actually have the config, nor should
        #        if - we need to API-ify the suggestions for this to work
        #        properly.
        return None

        if config and config.get(config.SECRETS, 'passphrase'):
            return cls(context, config)
        else:
            return None


SUGGESTIONS = dict((s.ID, s) for s in [
    SuggestionWelcome,
#   SuggestionAddAccount, -- FIXME
    SuggestionBrowse,
    SuggestionEncrypt,
    ])
