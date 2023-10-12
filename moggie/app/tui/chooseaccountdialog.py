from ...api.requests import RequestCommand

from .multichoicedialog import MultiChoiceDialog


class ChooseAccountDialog(MultiChoiceDialog):
    def __init__(self, tui, context, title,
            action=None, default=None, create=True,
            multi=False, allow_none=False):

        tui.send_with_context(
            RequestCommand('context', args=['list', '--output=details']),
            on_reply=self.update_account_list,
            on_error=lambda e: False,  # Suppress errors
            timeout=5)

        # Grab accounts from context view, that might be faster?
        account_list = []
        def action_with_context(account):
            return action(context, account)

        super().__init__(tui, account_list,
            title=title,
            multi=multi,
            action=action_with_context,
            prompt='E-mail',
            create=(lambda t: t.lower()) if create else None,
            default=default,
            allow_none=allow_none)

    def update_account_list(self, result):
        for context, ctx_info in result['data'][0].items():
            for account, acct_info in ctx_info.get('accounts', {}).items():
                for addr in acct_info.get('addresses', []):
                    if addr not in self.choices:
                        self.choices.append(addr)
        self.choices.sort()
        self.update_pile(message=self.title, widgets=True)
