import asyncio
import urwid

from .decorations import ENVELOPES, HELLO, HELLO_CREDITS


def emit_soon(widget, signal, seconds=0.75):
    async def emitter(sec, sig):
        await asyncio.sleep(sec)
        widget._emit(sig)
    asyncio.create_task(emitter(seconds, signal))


class PopUpManager(urwid.PopUpLauncher):
    def __init__(self, tui_frame, content):
        super().__init__(content)
        self.tui_frame = tui_frame
        self.target = None
        self.target_args = []

    def open_with(self, target, *target_args):
        self.target = target
        self.target_args = target_args
        return self.open_pop_up()

    def create_pop_up(self):
        target, args = self.target, self.target_args
        if self.target:
            pop_up = self.target(self.tui_frame, *self.target_args)
            urwid.connect_signal(pop_up, 'close', lambda b: self.close_pop_up())
            return pop_up
        return None

    def get_pop_up_parameters(self):
        # FIXME: Make this dynamic somehow?
        cols, rows = self.tui_frame.screen.get_cols_rows()
        wwidth = min(cols, self.target.WANTED_WIDTH)
        return {
            'left': (cols//2)-(wwidth//2),
            'top': 2,
            'overlay_width': wwidth,
            'overlay_height': self.target.WANTED_HEIGHT}


class Selectable(urwid.WidgetWrap):
    def __init__(self, contents, on_select=None):
        self.contents = contents
        self.on_select = on_select or {}
        self._focusable = urwid.AttrMap(self.contents, '', dict(
            ((a, 'focus') for a in [None,
                'email', 'subtle', 'hotkey', 'active', 'act_hk',
                'list_from', 'list_attrs', 'list_subject', 'list_date',
                'check_from', 'check_attrs', 'check_subject', 'check_date'])))
        super(Selectable, self).__init__(self._focusable)

    def selectable(self):
        return True

    def keypress(self, size, key):
        if key in self.on_select:
            self.on_select[key](self)
        else:
            return key


class CloseButton(Selectable):
    PLACEHOLDER = urwid.Text('   ')
    def __init__(self, on_select=None):
        Selectable.__init__(self, urwid.Text(('subtle', '[x]')),
            on_select={'enter': on_select})


class QuestionDialog(urwid.WidgetWrap):
    WANTED_HEIGHT = 4
    WANTED_WIDTH = 40
    signals = ['close']
    def __init__(self):
        close_button = urwid.Button(('subtle', '[x]'))
        urwid.connect_signal(close_button, 'click', lambda b: self._emit('close'))
        fill = urwid.Filler(urwid.Pile([
            urwid.Text('WTF OMG LOL'),
            close_button]))
        super().__init__(urwid.AttrWrap(fill, 'popbg'))


class SplashCat(urwid.Filler):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    def __init__(self, suggestions, message=''):
        self.suggestions = suggestions
        widgets = [
            ('weight', 3, urwid.Text(
                [message, '\n', HELLO, ('subtle', HELLO_CREDITS), '\n'],
                'center'))]
        if len(self.suggestions):
            widgets.append(('pack',  self.suggestions))
        urwid.Filler.__init__(self, urwid.Pile(widgets), valign='middle')

    def incoming_message(self, message):
        self.suggestions.incoming_message(message)


class SplashMoreWide(urwid.Filler):
    COLUMN_NEEDS = 60
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    CONTENT = ENVELOPES + '\n\n\n\n'
    def __init__(self):
        urwid.Filler.__init__(self,
            urwid.Text([self.CONTENT], 'center'),
            valign='middle')


class SplashMoreNarrow(SplashMoreWide):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 40
    CONTENT = '\n\n\n\n' + ENVELOPES
