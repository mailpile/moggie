#!/bin/bash
export GNUPGHOME=$(pwd)/GnuPG
exec mutt -f .
