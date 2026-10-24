#!/bin/sh
# S7: refuse the first stop of the run, allow every later one. The marker lives in the per-run gate state dir.
cat >/dev/null
marker="$PATTON_GATE_STATE/stop-refused-once"
if [ -e "$marker" ]; then
  exit 0
fi
: > "$marker"
printf '%s' '{"decision":"block","reason":"Patton stop check: the mission is not finished. Reply STOP-AGAIN."}'
