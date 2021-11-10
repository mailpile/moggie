import os
import sys


def fix_sys_path():
  base_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))

  sm_dir = os.path.join(base_dir, 'submodules')
  if not os.path.exists(sm_dir):
    return

  subs = [os.path.join(sm_dir, d) for d in os.listdir(sm_dir) if d[:1] != '.']
  for sm in subs:
    if sm not in sys.path:
      sys.path.append(sm)


fix_sys_path()
