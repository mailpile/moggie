import urwid

from .widgets import *


class EditSearch(EditLine):
    signals = ['close'] + EditLine.signals

    def keypress(self, size, key):
        if key == '/':
            self._emit('close')
            return None
        return super().keypress(size, key)


class SearchDialog(urwid.WidgetWrap):
    HELP_TEXT = """\

Examples:
 - in:inbox tag:unread
 - from:joe has:attachment
 - dates:2010-01..2010-04
 - party +from:mom -to:dad
 - h* *orld

Note: Multiple terms will narrow the search, unless
prefixed with a + to "add" or - to "remove" hits.
Use an asterisk (*) to search for word fragments.
"""
    WANTED_HEIGHT = 6 + len(HELP_TEXT.splitlines())
    WANTED_WIDTH = 60

    signals = ['close']

    def search(self, terms):
        terms = terms.replace('\n', '').strip()
        if not self.exact.get_state():
            def _fuzz(term):
                if ':' in term or '*' in term or term[:1] in ('-', '+'):
                    return term
                if term[-1:] == 's':
                    term = term[:-1]
                return term + '*'
            terms = ' '.join(_fuzz(w) for w in terms.split(' ') if w)
        if terms:
            self.tui_frame.show_search_result(terms)
        self._emit('close')

    def __init__(self, tui_frame):
        self.tui_frame = tui_frame
        close_button = CloseButton(
            on_select=lambda b: self._emit('close'), style='popsubtle')

        self.exact = urwid.CheckBox('Exact matches only', False)
        self.search_box = EditSearch('Search: ',
            multiline=False, allow_tab=False, wrap='ellipsis')
        #urwid.connect_signal(
        #    self.search_box, 'change', lambda b,t: self.search(t))
        urwid.connect_signal(self.search_box, 'enter',
            lambda e: self.search(self.search_box.edit_text))
        urwid.connect_signal(self.search_box, 'close',
            lambda e: self._emit('close'))

        fill = urwid.Filler(urwid.Pile([
            self.search_box,
            urwid.Divider(),
            self.exact,
            urwid.Text(('popsubtle', self.HELP_TEXT))]))
        super().__init__(urwid.LineBox(urwid.AttrWrap(fill, 'popbg')))
