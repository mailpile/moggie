# Submodules, why?

These are packages which depend upon, but either are not packages by
mainstream distributions or need moggie-specific patches for some reason.

Details:

   - python-passcrow: Our own code, too new to be packaged anywhere
   - python-sop: Versions in distros are too old
   - pyzipper: We are adding read/write features to this library
   - upagekite: Also Bjarni's code, not in distros AFAICT
   - websockets: The version currently packaged by Ubuntu seems broken?

