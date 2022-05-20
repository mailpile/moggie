import copy
import urwid

from ...jmap.requests import RequestBase
from ..suggestions import *
from .widgets import *


class SuggestionBox(urwid.Pile):
    DISMISSED = set()

    def __init__(self, fallbacks=None, suggestions=None):
        self.widgets = []
        urwid.Pile.__init__(self, self.widgets)

        self.fallbacks = fallbacks or []
        self.suggestions = suggestions or []
        self.update_suggestions(self.get_suggestions())

    def get_suggestions(self):
        # FIXME: Queue a request for a list of suggestions from
        #        the backend.
        # FIXME: This should be context dependent.
        suggest = copy.copy(self.suggestions)
        for _id in sorted(SUGGESTIONS.keys()):
            if _id in SuggestionBox.DISMISSED:
                continue
            sg_obj = SUGGESTIONS[_id].If_Wanted(None, None)
            if sg_obj is not None:
                suggest.append(sg_obj)
                if len(suggest) >= 3:
                    break
        if not len(suggest):
            suggest.extend(self.fallbacks)
        return suggest

    def set_suggestions(self, suggestions):
        # FIXME: this is dumb
        self.suggestions = suggestions
        self.update_suggestions(self.get_suggestions())

    def _on_activate(self, suggestion):
        def activate(i):
            act = suggestion.action()  # FIXME
            if isinstance(act, RequestBase):
                pass  # FIXME: Send this request to the backend
        return activate

    def _on_dismiss(self, suggestion):
        def dismiss(i):
            SuggestionBox.DISMISSED.add(suggestion.ID)
            self.update_suggestions(self.get_suggestions())
        return dismiss

    def update_suggestions(self, suggest):
        widgets = []
        for sgn in suggest:
            columns = [
                ('fixed',  4, urwid.Text(('subtle', '*'), 'right')),
                ('weight', 1, Selectable(urwid.Text(sgn.message()),
                    on_select={'enter': self._on_activate(sgn)}))]
            if sgn.ID is not None:
                columns.append(
                    ('fixed',  3, CloseButton(
                        on_select=self._on_dismiss(sgn))))
            else:
                columns.append(('fixed',  3, CloseButton.PLACEHOLDER))
            widgets.append(urwid.Columns(columns, dividechars=1))

        self.widgets = widgets
        self.contents = [(w, ('pack', None)) for w in self.widgets]

    def __len__(self):
        return len(self.widgets)

    def incoming_message(self, message):
        pass  # FIXME: Listen for suggestions
