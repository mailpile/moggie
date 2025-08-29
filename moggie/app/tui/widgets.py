import asyncio
import urwid
import urwid_readline

from .decorations import ENVELOPES, HELLO, HELLO_CREDITS, FOCUS_MAP


# Not strictly a widget, but we use it all over
def try_get(maybe_dict, key, default):
    try:
        return maybe_dict[key]
    except (KeyError, TypeError):
        return default


# Not strictly a widget, but we use it all over
def emit_soon(widget, signal, seconds=0.15):
    async def emitter(sec, sig):
        await asyncio.sleep(sec)
        widget._emit(sig)
    asyncio.create_task(emitter(seconds, signal))


def fixed_column(widget, text, *args, **kwargs):
    """
    Returns a fixed width element for use as a column. The width
    is derived from the length of the text + widget.WIDTH_OVERHEAD.
    """
    width = len(text)
    widget = widget(text, *args, **kwargs)
    if hasattr(widget, 'width_overhead'):
        width += widget.width_overhead
    return ('fixed', width, widget)


def make_plan_args(plan, command, skip=[]):
    args = []
    for k, arglist in plan[command].items():
        if k in skip:
            pass
        elif k == 'ARGS':
            args.extend(arglist)
        else:
            args.extend('--%s=%s' % (k, v) for v in arglist)
    return args


class EditLine(urwid_readline.ReadlineEdit):
    signals = ['enter'] + urwid_readline.ReadlineEdit.signals

    def keypress(self, size, key):
        if key == 'backspace':  # Avoid backspace bubbling
            if self.edit_pos == 0:
                return None
        if key == 'enter':
            self._emit('enter')
            return None
        return super().keypress(size, key)


class PopUpManager(urwid.PopUpLauncher):
    def __init__(self, tui, content):
        super().__init__(content)
        self.tui = tui
        self.target = None

    def open_with(self, target, *target_args, **target_kwargs):
        self.target = target(self.tui, *target_args, **target_kwargs)
        urwid.connect_signal(
            self.target, 'close', lambda b: self.close_pop_up())
        return self.open_pop_up()

    def showing_popup(self):
        return (self.target is not None)

    def close_pop_up(self):
        self.target = None
        return super().close_pop_up()

    def create_pop_up(self):
        return self.target

    def get_pop_up_parameters(self):
        def _w(attr, default):
            if hasattr(self.target, attr):
                return getattr(self.target, attr)()
            else:
                return default
        cols, rows = self.tui.screen.get_cols_rows()
        wwidth = min(cols, _w('wanted_width', self.target.WANTED_WIDTH))
        return {
            'left': (cols//2)-(wwidth//2),
            'top': 2,
            'overlay_width': wwidth,
            'overlay_height': _w('wanted_height', self.target.WANTED_HEIGHT)}


class Selectable(urwid.WidgetWrap):
    def __init__(self, contents, on_select=None):
        self.contents = contents
        self.on_select = on_select or {}
        self._focusable = urwid.AttrMap(self.contents, {}, FOCUS_MAP)
        super(Selectable, self).__init__(self._focusable)

    def selectable(self):
        return True

    def keypress(self, size, key):
        if key in self.on_select:
            try:
                self.on_select[key](self)
            except TypeError:
                pass
        else:
            return key


class SimpleButton(Selectable):
    LABEL = 'OK'
    def __init__(self, label=None, on_select=None, style=None, box='[%s]'):
        self.width_overhead = len(box % '')
        self.label = label or self.LABEL
        self.text = urwid.Text((style or 'subtle', box % self.label))
        Selectable.__init__(self, self.text, on_select={'enter': on_select})

    def set_text(self, new_text):
        self.text.set_text(new_text)


class CloseButton(SimpleButton):
    PLACEHOLDER = urwid.Text('   ')
    LABEL = 'x'
    def __init__(self, on_select=None, style=None):
        super().__init__(on_select=on_select, style=style)


class CancelButton(SimpleButton):
    LABEL = 'Cancel'
    def __init__(self, on_select=None, style=None):
        super().__init__(on_select=on_select, style=style)


class SplashCat(urwid.Filler):
    COLUMN_NEEDS = 40
    COLUMN_WANTS = 70
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    def __init__(self, suggestions=None, message='', decoration=None):
        self.suggestions = suggestions or []

        if decoration is None:
            decoration = [HELLO, ('subtle', HELLO_CREDITS)]
        elif isinstance(decoration, str):
            decoration = [decoration]

        widgets = [
            ('weight', 3, urwid.Text(
                [message, '\n'] + decoration + ['\n'],
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
    COLUMN_NEEDS = 30
    COLUMN_WANTS = 30
    COLUMN_FIT = 'weight'
    COLUMN_STYLE = 'content'
    CONTENT = '\n\n\n\n' + ENVELOPES
