#!/usr/bin/env bash
# Run a command while a local LiteLLM proxy forwards Anthropic-format calls to Groq.
#
#   lab/cc/with_groq_proxy.sh python3 -m patton_lab.cc.runner smoke --arms O-R1 --fixtures h01 --out cc_smoke
#
# Groq refuses Mullvad exit addresses, so this disconnects Mullvad first and reconnects on exit,
# including on failure and Ctrl-C (standing approval for Groq work). The key is read from a file into
# the proxy's environment only. It never appears in argv, in this script's output, or in the command's
# environment.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
key_file="${PATTON_LAB_GROQ_KEY_FILE:-$HOME/.groq-staging-key}"
port="${PATTON_PROXY_PORT:-4000}"
[ "$#" -gt 0 ] || { printf 'usage: %s <command...>\n' "$0" >&2; exit 1; }
[ -s "$key_file" ] || { printf '!! no Groq key at %s\n' "$key_file" >&2; exit 2; }

was_connected=0
if mullvad status 2>/dev/null | grep -q '^Connected'; then was_connected=1; fi
proxy_pid=""
cleanup() {
    if [ -n "$proxy_pid" ] && kill -0 "$proxy_pid" 2>/dev/null; then
        kill "$proxy_pid" 2>/dev/null
        wait "$proxy_pid" 2>/dev/null
    fi
    if [ "$was_connected" = 1 ]; then
        mullvad connect >/dev/null 2>&1
        back=0
        for _ in $(seq 1 30); do
            if mullvad status 2>/dev/null | grep -q '^Connected'; then back=1; break; fi
            sleep 1
        done
        [ "$back" = 1 ] || printf '!! Mullvad did not reconnect within 30 s\n' >&2
    fi
}
trap cleanup EXIT INT TERM

[ "$was_connected" = 1 ] && mullvad disconnect >/dev/null 2>&1
GROQ_API_KEY="$(cat "$key_file")" "${PATTON_LITELLM:-$HOME/.cache/patton-litellm/bin/litellm}" --config "$here/litellm.yaml" --host 127.0.0.1 --port "$port" \
    >"${PATTON_PROXY_LOG:-/tmp/patton-proxy.log}" 2>&1 &
proxy_pid=$!
exe=""
for _ in $(seq 1 50); do  # right after the fork the pid is still this shell; wait for the exec
    exe="$(readlink "/proc/$proxy_pid/exe" 2>/dev/null || true)"
    case "$exe" in */bash|*/sh|"") sleep 0.1 ;; *) break ;; esac
done
case "$exe" in
    *python*|*litellm*) ;;
    *) printf '!! pid %s is %s, not the proxy\n' "$proxy_pid" "${exe:-unknown}" >&2; exit 3 ;;
esac
ready=0
for _ in $(seq 1 60); do
    if ! kill -0 "$proxy_pid" 2>/dev/null; then printf '!! proxy exited; see the log\n' >&2; exit 4; fi
    if curl -fsS "http://127.0.0.1:$port/health/liveliness" >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[ "$ready" = 1 ] || { printf '!! proxy not ready after 60 s\n' >&2; exit 5; }
"$@"
