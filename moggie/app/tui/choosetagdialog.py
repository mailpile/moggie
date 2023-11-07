import logging

from .multichoicedialog import MultiChoiceDialog
from .contextlist import ContextList


class ChooseTagDialog(MultiChoiceDialog):
    def __init__(self, tui, mog_ctx, title,
            action=None, default=None, create=True,
            multi=False, allow_none=False):

        mog_ctx.search('all:mail', output='tags',
            on_success=self.update_tag_list)
            # FIXME: timeout=5)

        tag_list = sorted([tag for (tag, info) in ContextList.TAG_ITEMS])
        def action_with_context(tag):
            return action(mog_ctx.key, tag)

        super().__init__(tui, tag_list,
            title=title,
            multi=multi,
            prompt='Tag',
            action=action_with_context,
            create=(lambda t: t.lower()) if create else None,
            default=default,
            allow_none=allow_none)

    def update_tag_list(self, mog_ctx, search_result):
        for tag in search_result['data']:
            tag = str(tag, 'utf-8').split(':', 1)[1]
            if tag not in self.choices:
                self.choices.append(tag)
        self.choices.sort()
        self.update_pile(message=self.title, widgets=True)
