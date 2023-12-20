from .multichoicedialog import MultiChoiceDialog
from .widgets import try_get


class ChooseAccountDialog(MultiChoiceDialog):
    def __init__(self, tui, mog_ctx, title,
            action=None, default=None, create=True,
            multi=False, allow_none=False):

        mog_ctx.moggie.context('list',
            output='details',
            on_success=self.update_account_list)
            # FIXME: timeout=5)

        # Grab accounts from context view, that might be faster?
        account_list = []
        def action_with_context(account, pressed=None):
            return action(mog_ctx.key, account)

        super().__init__(tui, account_list,
            title=title,
            multi=multi,
            action=action_with_context,
            prompt='E-mail',
            create=(lambda t: t.lower()) if create else None,
            default=default,
            allow_none=allow_none)

    def update_account_list(self, moggie, result):
        data = try_get(result, 'data', result)
        for context, ctx_info in data[0].items():
            for account, acct_info in ctx_info.get('accounts', {}).items():
                for addr in acct_info.get('addresses', []):
                    if addr not in self.choices:
                        self.choices.append(addr)
        self.choices.sort()
        self.update_pile(message=self.title, widgets=True)
