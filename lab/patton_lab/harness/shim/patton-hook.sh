#!/bin/sh
# Fail closed around bridge.py, for harnesses that let a call run when their own command hook crashes or hangs
# (dsh, Cline, Open Interpreter, Gemini CLI, goose without on_failure: block).
#
#   patton-hook.sh <dialect>        a tool-call hook: no finished answer from the bridge blocks the call
#   patton-hook.sh <dialect> stop   a stop hook: no finished answer lets the agent stop
#
# The bridge marks a finished answer with a last line (#patton-bridge-done) that this shim strips. A missing python3,
# a crash at import, a kill, or a run past PATTON_HOOK_LIMIT seconds (default 45, under every port's own hook
# timeout) leaves no mark, and the shim prints the dialect's own refusal. Only POSIX sh, cat, mktemp, sed, tail and
# timeout are needed; when the shim itself is killed by the harness, the harness decides.
set -u
dialect=${1:-}
mode=${2:-tool}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
bridge=${PATTON_SHIM_BRIDGE:-$here/../bridge.py}
python=${PATTON_PYTHON:-python3}
limit=${PATTON_HOOK_LIMIT:-45}
sentinel='#patton-bridge-done'

refuse() {
  [ "$mode" = stop ] && exit 0
  reason="Patton gate: the hook could not check this call ($1), so it blocks it."
  text=$(printf '%s' "$reason" | sed 's/\\/\\\\/g; s/"/\\"/g')
  case "$dialect" in
    gemini_cli) printf '{"decision": "deny", "reason": "%s"}\n' "$text"; exit 0 ;;
    open_interpreter) printf '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "%s"}}\n' "$text"; exit 0 ;;
    goose) printf '{"decision": "block", "reason": "%s"}\n' "$text"; exit 0 ;;
    cline) printf '{"cancel": true, "errorMessage": "%s"}\n' "$text"; exit 2 ;;
    *) printf '%s\n' "$reason" >&2; exit 2 ;;
  esac
}

command -v "$python" >/dev/null 2>&1 || refuse "python3 is not on PATH"
command -v timeout >/dev/null 2>&1 || refuse "timeout is not on PATH"
tmp=$(mktemp 2>/dev/null) || refuse "no temporary file"
trap 'rm -f "$tmp" "$tmp.out"' EXIT
cat > "$tmp" || refuse "the hook input could not be read"
PATTON_BRIDGE_SENTINEL=1 timeout -k 2 "$limit" "$python" "$bridge" "$dialect" < "$tmp" > "$tmp.out"
rc=$?
[ "$(tail -n 1 "$tmp.out" 2>/dev/null)" = "$sentinel" ] || refuse "the gate ended with exit code $rc before it answered"
sed '$d' "$tmp.out"
exit "$rc"
