import copy
import urwid

from ...api.requests import RequestBase
from ..suggestions import *
from .decorations import EMOJI
from .widgets import *


class SuggestionBox(urwid.Pile):
    DISMISSED = set()

    def __init__(self, tui_frame,
            update_parent=None,
            fallbacks=None, suggestions=None, max_suggestions=3,
            omit_actions=None):

        self.tui_frame = tui_frame
        self.update_parent = update_parent or (lambda: None)
        self.omit_actions = omit_actions or []
        self.max_suggestions = max_suggestions
        self.widgets = []
        urwid.Pile.__init__(self, self.widgets)

        self.fallbacks = fallbacks or []
        self.suggestions = suggestions or []
        self.update_suggestions(self.get_suggestions())

    def get_suggestions(self, context=None):
        # FIXME: Queue a request for a list of suggestions from
        #        the backend.
        # FIXME: This should be context dependent.
        suggest = copy.copy(self.suggestions)
        for _id in sorted(SUGGESTIONS.keys()):
            if _id in SuggestionBox.DISMISSED:
                continue
            sg_obj = SUGGESTIONS[_id].If_Wanted(
                context, None, ui_moved=self.tui_frame.user_moved)
            if ((sg_obj is not None) and
                    (sg_obj.UI_ACTION not in self.omit_actions)):
                suggest.append(sg_obj)
                if len(suggest) >= self.max_suggestions:
                    break
        if not len(suggest):
            for sg_cls in self.fallbacks:
                sg_obj = sg_cls.If_Wanted(context, None)
                if sg_obj and sg_obj.ID not in SuggestionBox.DISMISSED:
                    suggest.append(sg_obj)
        return suggest

    def set_suggestions(self, suggestions, context=None):
        # FIXME: this is dumb
        self.suggestions = suggestions
        self.update_suggestions(self.get_suggestions())

    def _on_activate(self, suggestion):
        def activate(*ignored):
            act = suggestion.action()  # FIXME
            if act == Suggestion.UI_QUIT:
                self.tui_frame.ui_quit()
            elif act == Suggestion.UI_DISMISS:
                self._on_dismiss(suggestion)()
            elif act == Suggestion.UI_BROWSE:
                self.tui_frame.show_browser(history=False)
            elif act == Suggestion.UI_ENCRYPT:
                self.tui_frame.ui_change_passphrase()
            elif isinstance(act, RequestBase):
                pass  # FIXME: Send this request to the backend
        return activate

    def _on_dismiss(self, suggestion):
        def dismiss(*ignored):
            SuggestionBox.DISMISSED.add(suggestion.ID)
            self.update_suggestions(self.get_suggestions())
            self.update_parent()
        return dismiss

    def update_suggestions(self, suggest):
        widgets = []
        for sgn in suggest:
            columns = [
                ('fixed',  2, urwid.Text(('subtle', EMOJI.get('hint', '->')),
                                         align='left')),
                ('weight', 1, Selectable(urwid.Text(('subtle', sgn.message())),
                    on_select={'enter': self._on_activate(sgn)}))]
            if sgn.ID is not None:
                columns.append(
                    ('fixed',  3, CloseButton(
                        on_select=self._on_dismiss(sgn))))
            else:
                columns.append(('fixed',  3, CloseButton.PLACEHOLDER))
            widgets.append(urwid.Columns(columns, dividechars=1))
        have_suggestions = bool(widgets)
        if have_suggestions:
            widgets.append(urwid.Divider())

        self.widgets = widgets
        self.contents = [(w, ('pack', None)) for w in self.widgets]
        if have_suggestions:
            self.set_focus(0)

    def __len__(self):
        return len(self.widgets)

    def incoming_message(self, message):
        pass  # FIXME: Listen for suggestions
