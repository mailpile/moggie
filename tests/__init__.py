import logging
import sys

logging.basicConfig(level=logging.ERROR)
sys.stderr.write("""\
HINT: Best run with `python3 -W ignore:ResourceWarning -m unittest`
""")
