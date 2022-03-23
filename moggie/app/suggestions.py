# FIXME: i18n i18n i18n ...


class Suggestion:
    MESSAGE = 'Override this message!'
    ID = None

    def __init__(self, context, config):
        self.context = context
        self.config = config

    @classmethod
    def If_Wanted(cls, context, config):
        return cls(context, config)

    def message(self):
        return self.MESSAGE

    def action(self):
        return None


class SuggestionWelcome(Suggestion):
    MESSAGE = 'Welcome to Moggie! Press `q` to quit.'
    ID = 0
    # FIXME: Make wanted() check if anything has been configured at all.


class SuggestionAddAccount(Suggestion):
    MESSAGE = 'Add one or more e-mail accounts'
    ID = 1
    # FIXME: Make wanted() check whether accounts have been added.


class SuggestionBrowse(Suggestion):
    MESSAGE = 'Browse for mail on this computer'
    ID = 2 
    # FIXME: Make wanted() check whether mailboxes have been added.


class SuggestionEncrypt(Suggestion):
    MESSAGE = 'Encrypt your local data'
    ID = 3
    # FIXME: Make wanted() check whether mailboxes have been added.


SUGGESTIONS = dict((s.ID, s) for s in [
    SuggestionWelcome,
    SuggestionAddAccount,
    SuggestionBrowse,
    SuggestionEncrypt,
    ])
