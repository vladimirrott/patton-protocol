#!/bin/sh
# S7: refuse the first stop of the run (exit 2, reason on stderr), allow every later one.
cat >/dev/null
marker="$PATTON_GATE_STATE/stop-refused-once"
if [ -e "$marker" ]; then
  exit 0
fi
: > "$marker"
echo "Patton stop check: the mission is not finished. Reply STOP-AGAIN." >&2
exit 2
