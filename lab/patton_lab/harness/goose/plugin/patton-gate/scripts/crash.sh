#!/bin/sh
# S5: a hook command that crashes before deciding anything.
cat >/dev/null
echo "crash.sh: simulated hook crash" >&2
exit 1
