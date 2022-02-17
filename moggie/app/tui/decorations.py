from ...config import APPNAME, APPVER


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
   )  _`-.                 x
  .  : `. .                x
  : _   '  \               x
  ; *` _.   `*-._          x
  `-.-'          `-.       x
    ;       `       `.     x
    :.       .        \    x
    . \  .   :   .-'   .   x
    '  `+.;  ;  '      :   x
    :  '  |    ;       ;-. x
    ; '   : :`-:     _.`* ;x
.*' /  .*' ; .*`- +'  `*'  x
 `*-*   `*-*  `*-*'        x

%s v%s                     x
""").replace('x', '') % (APPNAME, APPVER)

HELLO_CREDITS = """
   (cat by Blazej Kozlowski)
"""


# FIXME: generate different palettes based on the contents of our
#        config file; we should let the user specify their own
#        colors, and also provide light/dark themes.
def palette(config):
    return [
            (None,             'light gray',  'black',     ''),
            ('',               'light gray',  'black',     ''),
            ('body',           'light gray',  'black',     ''),
            ('sidebar',        'light gray',  'black',     ''),
            ('content',        'light gray',  'black',     ''),
            ('email',          'brown',       'black',     ''),
            ('active',         'light blue',  'black',     ''),
            ('active',         'white',       'brown',     ''),
            ('hotkey',         'brown',       'black',     ''),
            ('act_hk',         'black',       'brown',     ''),
            ('crumbs',         'white',       'dark blue', ''),
            ('header',         'light gray',  'black',     ''),
            ('top_hk',         'brown',       'black',     ''),
            ('subtle',         'dark gray',   'black',     ''),
            ('list_from',      'light gray',  'black',     ''),
            ('list_attrs',     'dark gray',   'black',     ''),
            ('list_subject',   'light gray',  'black',     ''),
            ('list_date',      'dark gray',   'black',     ''),
            ('email_key_from', 'dark gray',   'black',     ''),
            ('email_val_from', 'light blue',  'black',     ''),
            ('email_key_to',   'dark gray',  'black',     ''),
            ('email_val_to',   'dark gray',  'black',     ''),
            ('email_key_cc',   'dark gray',  'black',     ''),
            ('email_val_cc',   'dark gray',  'black',     ''),
            ('email_key_date', 'dark gray',  'black',     ''),
            ('email_val_date', 'dark gray',  'black',     ''),
            ('email_key_subject', 'dark gray',  'black',     ''),
            ('email_val_subject', 'light green',  'black',     ''),
            ('focus',          'white',       'dark blue', '')]

