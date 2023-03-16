import os
import sys


def fix_sys_path():
  base_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))

  libs_dir = os.path.join(base_dir, 'lib')
  if not os.path.exists(libs_dir):
    return
  if libs_dir not in sys.path:
    sys.path.append(libs_dir)

  return  # FIXME: Old code follows, which we may still need on Windows?

  sm_dir = os.path.join(base_dir, 'submodules')
  if not os.path.exists(sm_dir):
    return

  for sub in (d for d in os.listdir(sm_dir) if d[:1] != '.'):
    if not os.path.exists(os.path.join(libs_dir, sub)):
      sm = os.path.join(sm_dir, sub)
      if sm not in sys.path:
        sys.path.append(sm)


fix_sys_path()
