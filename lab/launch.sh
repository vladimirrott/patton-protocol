#!/usr/bin/env bash
# Run a Lab command with the provider keys in the environment.
#
#   lab/launch.sh record --exp E1
#   lab/launch.sh smoke --provider groq
#
# Keys are read from files into environment variables. They never appear in
# argv, in output, or in any file this script writes.
#
# Groq refuses Mullvad exit addresses. When the command needs Groq and Mullvad
# is connected, this script disconnects it and reconnects it on exit, including
# on failure and on Ctrl-C.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
groq_file="${PATTON_LAB_GROQ_KEY_FILE:-$HOME/.groq-staging-key}"
openai_file="${PATTON_LAB_OPENAI_KEY_FILE:-$HOME/whystuck/scratchpad/openai.key}"

cd "$here" || exit 4

if [ "$#" -eq 0 ]; then
    printf 'usage: %s <command> [options]\n' "$0" >&2
    exit 1
fi

if [ "$1" = "smoke" ]; then
    needs="groq"
    for arg in "$@"; do [ "$arg" = "openai" ] && needs="openai"; done
else
    shift_args=("${@:2}")
    if ! needs="$(python3 -m patton_lab.run providers "${shift_args[@]}")"; then
        printf 'could not work out which providers this command needs; nothing was run\n' >&2
        exit 4
    fi
fi

load_key() {  # $1 variable name, $2 file
    if [ -r "$2" ]; then
        export "$1=$(tr -d '\n\r ' < "$2")"
    fi
}
case " $needs " in *" groq "*) load_key PATTON_LAB_GROQ_KEY "$groq_file" ;; esac
case " $needs " in *" openai "*) load_key PATTON_LAB_OPENAI_KEY "$openai_file" ;; esac

reconnect=0
restore() {
    if [ "$reconnect" -eq 1 ]; then
        mullvad connect >/dev/null 2>&1
        printf 'mullvad: reconnect requested\n' >&2
    fi
}
trap restore EXIT
trap 'exit 130' INT TERM

case " $needs " in
    *" groq "*)
        if command -v mullvad >/dev/null 2>&1 && mullvad status 2>/dev/null | head -1 | grep -q '^Connected'; then
            reconnect=1
            mullvad disconnect >/dev/null 2>&1
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                state="$(mullvad status 2>/dev/null | head -1)"
                [ "$state" = "Disconnected" ] && break
                sleep 1
            done
            if [ "$(mullvad status 2>/dev/null | head -1)" != "Disconnected" ]; then
                printf 'mullvad did not disconnect; Groq would refuse the calls. Nothing was run.\n' >&2
                exit 4
            fi
            printf 'mullvad: disconnected for Groq, will reconnect on exit\n' >&2
        fi
        ;;
esac

python3 -m patton_lab.run "$@"
rc=$?
exit "$rc"
