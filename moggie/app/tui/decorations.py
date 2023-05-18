from ...config import APPNAME, APPVER

EMOJI = {
    'mailbox':    '\U0001F4C1',
    'search':     '\U0001F50E',
    'attachment': '\U0001F4CE',
    'lock':       '\U0001F512'}


ENVELOPES = ("""\
     _______      x
    |==   []|     x
    |  ==== |____ x
    '-------'  []|x
         |   === |x
         '-------'x
  _______         x
 |==   []|        x
 |  ==== |        x
 '-------'        x
""").replace('x', '')


HELLO = ("""\
  _                        x
  \`*-.                    x
   )  _`-.         %-8.8s
  .  : `. .        v%-7.7s
  : _   '  \               x
  ; *` _.   `*-._          x
  `-.-'          `-.       x
    ;       `       `.     x
    :.       .        \    x
    . \  .   :   .-'   .   x
    '  `+.;  ;  '      :   x
    :  '  |    ;       ;-. x
    ; '   : :`-:     _.`* ;x
  .*' /  .*' ; .*`- +'  `*'x
  `*-*   `*-*  `*-*'       x
""").replace('x', '') % (APPNAME, APPVER)

HELLO_CREDITS = """\
           cat by Blazej Kozlowski"""


# FIXME: generate different palettes based on the contents of our
#        config file; we should let the user specify their own
#        colors, and also provide light/dark themes.
DEFAULT_PALETTE = [
            (None,             'light gray',  'black',     ''),
            ('',               'light gray',  'black',     ''),
            ('body',           'light gray',  'black',     ''),
            ('sidebar',        'light gray',  'black',     ''),
            ('content',        'light gray',  'black',     ''),
            ('email',          'brown',       'black',     ''),
            ('hotkey',         'brown',       'black',     ''),
            ('act_hk',         'black',       'brown',     ''),
            ('crumbs',         'white',       'dark blue', ''),
            ('popbg',          'white',       'dark blue', ''),
            ('popsubtle',      'light gray',  'dark blue', ''),
            ('header',         'light gray',  'black',     ''),
            ('top_hk',         'brown',       'black',     ''),
            ('subtle',         'dark gray',   'black',     ''),
            ('list_from',      'light gray',  'black',     ''),
            ('list_attrs',     'dark gray',   'black',     ''),
            ('list_subject',   'light gray',  'black',     ''),
            ('list_date',      'dark gray',   'black',     ''),
            ('more_from',      'dark gray',   'black',     ''),
            ('more_attrs',     'dark gray',   'black',     ''),
            ('more_subject',   'dark gray',   'black',     ''),
            ('more_date',      'dark gray',   'black',     ''),
            ('check_from',     'light green', 'black',     ''),
            ('check_attrs',    'dark green',  'black',     ''),
            ('check_subject',  'light green', 'black',     ''),
            ('check_date',     'dark green',  'black',     ''),
            ('email_key_from', 'dark gray',   'black',     ''),
            ('email_val_from', 'light blue',  'black',     ''),
            ('email_key_att',  'dark gray',   'black',     ''),
            ('email_val_att',  'light blue',  'black',     ''),
            ('email_key_to',   'dark gray',   'black',     ''),
            ('email_val_to',   'dark gray',   'black',     ''),
            ('email_key_cc',   'dark gray',   'black',     ''),
            ('email_val_cc',   'dark gray',   'black',     ''),
            ('email_key_date', 'dark gray',   'black',     ''),
            ('email_val_date', 'dark gray',   'black',     ''),
            ('email_key_subj', 'dark gray',   'black',     ''),
            ('email_val_subj', 'light green', 'black',     ''),
            ('active',         'light blue',  'black',     ''),
            ('active',         'white',       'brown',     ''),
            ('focus1',         'white',       'dark blue', ''),
            ('focus2',         'white',       'brown', '')]

def palette(config):
    return DEFAULT_PALETTE


FOCUS_NONE = ('', 'body', 'sidebar', 'content', 'focus1', 'focus2')
FOCUS_BG_COLOR_MAP = {
    'black':     'focus1',
    'brown':     'focus1',
    'dark blue': 'focus2'}

def make_focus_map():
    focus_map = {}
    for name, fg, bg, _ in DEFAULT_PALETTE:
        if name not in FOCUS_NONE:
            focus_map[name] = FOCUS_BG_COLOR_MAP.get(bg, 'focus1')
    return focus_map

FOCUS_MAP = make_focus_map()
