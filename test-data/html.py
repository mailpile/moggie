import os
from moggie.security.html import *

TEST_DATA = os.path.join(os.path.dirname(__file__), 'html-testdata')

for tfile in os.listdir(TEST_DATA):
    if tfile.endswith('.html'):
        in_html  = open(os.path.join(TEST_DATA, tfile), 'r').read()
        out_md   = open(os.path.join(TEST_DATA, tfile + '.m'), 'r').read()
        out_html = open(os.path.join(TEST_DATA, tfile + '.c'), 'r').read()



