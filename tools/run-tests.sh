#!/bin/bash
set -e
for TEST in \
     moggie.email.headers \
     moggie.email.metadata \
     moggie.email.rfc2074 \
     moggie.email.addresses \
     moggie.util.dumbcode \
     moggie.util.intset \
     moggie.util.wordblob \
     moggie.search.dates \
     moggie.search.parse_greedy \
     moggie.search.engine \
     moggie.storage.files \
     moggie.storage.memory \
     moggie.storage.records \
     moggie.storage.metadata \
; do
     echo -e -n "$TEST\t"
     python3 -m $TEST
done
