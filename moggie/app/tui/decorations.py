from ...config import APPNAME, APPVER

EMOJI = {
    'unread':         '*',           # Star
    'in:urgent':      '!',           # Flag?
    'in:inbox':       'I',           #
    'in:drafts':      'D',           #
    'in:junk':        'J',           #
    'in:trash':       'T',           #
    'in:outgoing':    'O',           #
    'in:sent':        '✓',           # Checkmark
    'selected':       '✓',           # Checkmark
    'hint':           '\U0001F4A1',  # Lightbulb
    'browsing':       '\U0001F4BB',  # Laptop
   #'browsing':       '\U0001F5B4',  # Unicode hard disk
    'server':         '\U0001F4E7',  # E-mail emoji
    'file':           '\U0001F4BE',  # Floppy disk
    'folder':         '\U0001F4C1',  # Folder, closed
    #'tag':            '\U0001F5C3',  # Label
    #'tag':            '\U0001F3F7',  # Label
    'imap':           '\U0001F4EA',  # Mailbox, flag down
    'mbox':           '\U0001F4EA',  # Mailbox, flag down
    'maildir1.wervd': '\U0001F4EA',  # Mailbox, flag down
    'maildir':        '\U0001F4EA',  # Mailbox, flag down
    'mailzip':        '\U0001F4EA',  # Mailbox, flag down
    'mailbox':        '\U0001F4EA',  # Mailbox, flag down
    'search':         '\U0001F50E',  # Magnifying glass
    'attachment':     '\U0001F4CE',  # Paperclip
    'downleft':       '\U00002199',  # Down left arrow
    'encrypted':      '\U0001F512',  # Lock
    'verified':       '\U00002705',  # Check mark button
    'lock':           '\U0001F512'}  # Lock


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
   )  _`-.          %-8.8s
  .  : `. .         v%-7.7s
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
            ('go_group',       'light gray',  'dark blue', ''),
            ('go_hotkey',      'black',       'dark blue', ''),
            ('go_desc',        'white',       'dark blue', ''),
            ('selcount',       'black',       'dark green',''),
            ('header',         'light gray',  'black',     ''),
            ('top_hk',         'brown',       'black',     ''),
            ('col_hk',         'black',       'dark blue', ''),
            ('subtle',         'dark gray',   'black',     ''),
            ('list_from',      'light gray',  'black',     ''),
            ('list_attrs',     'dark gray',   'black',     ''),
            ('list_subject',   'light gray',  'black',     ''),
            ('list_date',      'dark gray',   'black',     ''),
            ('list_time',      'dark blue',   'black',     ''),
            ('list_tags',      'dark blue',   'black',     ''),
            ('list_to',        'light blue',  'black',     ''),
            ('more_from',      'dark gray',   'black',     ''),
            ('more_attrs',     'dark gray',   'black',     ''),
            ('more_subject',   'dark gray',   'black',     ''),
            ('more_date',      'dark gray',   'black',     ''),
            ('more_time',      'dark gray',   'black',     ''),
            ('more_tags',      'dark gray',   'black',     ''),
            ('more_to',        'dark gray',   'black',     ''),
            ('check_from',     'light green', 'black',     ''),
            ('check_attrs',    'dark green',  'black',     ''),
            ('check_subject',  'light green', 'black',     ''),
            ('check_date',     'dark green',  'black',     ''),
            ('check_time',     'dark green',  'black',     ''),
            ('check_tags',     'dark green',  'black',     ''),
            ('check_to',       'light green', 'black',     ''),
            ('email_key_from', 'dark gray',   'black',     ''),
            ('email_val_from', 'light blue',  'black',     ''),
            ('email_cs_from',  'dark gray',   'black',     ''),
            ('email_key_att',  'dark gray',   'black',     ''),
            ('email_val_att',  'light blue',  'black',     ''),
            ('email_cs_att',   'dark gray',   'black',     ''),
            ('email_key_to',   'dark gray',   'black',     ''),
            ('email_val_to',   'dark gray',   'black',     ''),
            ('email_cs_to',    'dark gray',   'black',     ''),
            ('email_key_cc',   'dark gray',   'black',     ''),
            ('email_val_cc',   'dark gray',   'black',     ''),
            ('email_cs_cc',    'dark gray',   'black',     ''),
            ('email_key_date', 'dark gray',   'black',     ''),
            ('email_val_date', 'dark gray',   'black',     ''),
            ('email_cs_date',  'dark gray',   'black',     ''),
            ('email_key_subj', 'dark gray',   'black',     ''),
            ('email_val_subj', 'light blue',  'black',     ''),
            ('email_cs_subj',  'dark gray',   'black',     ''),
            ('email_body',     'light gray',  'black',     ''),
            ('email_body_bg',  'dark gray',   'black',     ''),
            ('email_cstate',   'yellow',      'black',     ''),
            ('email_key_sel',  'light green', 'black',     ''),
            ('email_val_sel',  'light green', 'black',     ''),
            ('browse_cfg',     'light green', 'black',     ''),
            ('browse_cfg_i',   'dark gray',   'black',     ''),
            ('browse_name',    'white',       'black',     ''),
            ('browse_info',    'dark gray',   'black',     ''),
            ('browse_label',   'dark gray',   'black',     ''),
            ('active',         'light blue',  'black',     ''),
            ('active',         'white',       'brown',     ''),
            ('focus1',         'white',       'dark blue', ''),
            ('focus2',         'white',       'brown',     '')]

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
