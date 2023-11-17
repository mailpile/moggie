import logging

from .contextlist import ContextList
from .multichoicedialog import MultiChoiceDialog
from .widgets import SimpleButton


class ChooseTagDialog(MultiChoiceDialog):
    def __init__(self, tui, mog_ctx, title,
            action=None, default=None, create=True,
            multi=False, choices=None, ok_labels=None,
            allow_none=False, allow_move=False,
            show_hidden=False):

        self.show_hidden = show_hidden
        self.ok_labels = ok_labels or ['Tag']
        if allow_move:
            self.ok_labels.append(
                'Move' if (allow_move is True) else allow_move)

        if choices is None:
            mog_ctx.search('all:mail', output='tags',
                on_success=self.update_tag_list)
                # FIXME: timeout=5)
            tag_list = sorted([tag for (tag, info) in ContextList.TAG_ITEMS])
        else:
            tag_list = choices

        def action_with_context(tag, pressed=None):
            return action(mog_ctx.key, tag, pressed=pressed)

        super().__init__(tui, self._filter_hidden(tag_list),
            title=title,
            multi=multi,
            prompt='Other tags' if multi else 'Tag',
            action=action_with_context,
            create=(lambda t: t.lower()) if create else None,
            default=default,
            ok_labels=self.ok_labels,
            allow_none=allow_none)

    def _filter_hidden(self, tag_list):
        if self.show_hidden:
            return tag_list
        # Omit tags beginning or ending in a _
        return [t for t in tag_list if not '_' in (t[:1], t[-1:])]

    def update_tag_list(self, mog_ctx, search_result):
        for tag in search_result:
            tag = str(tag, 'utf-8').split(':', 1)[1]
            if tag not in self.choices:
                self.choices.append(tag)
        self.choices = self._filter_hidden(self.choices)
        self.choices.sort()
        self.update_pile(message=self.title, widgets=True)
