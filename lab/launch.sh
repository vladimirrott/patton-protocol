#!/usr/bin/env bash
# Run a Lab command. `record` and `smoke` get the provider keys they need.
#
#   lab/launch.sh record --exp E1
#   lab/launch.sh smoke --provider groq
#   lab/launch.sh replay            (no key, no network)
#
# Keys are read from files into environment variables. They never appear in
# argv, in output, or in any file this script writes. Any key already in the
# environment is discarded first, so a stale key cannot be used by accident.
#
# Groq refuses Mullvad exit addresses. When a record or smoke run needs Groq
# and Mullvad is connected, this script disconnects it and reconnects it on
# exit, including on failure and on Ctrl-C, then checks that it reconnected.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
groq_file="${PATTON_LAB_GROQ_KEY_FILE:-$HOME/.groq-staging-key}"
openai_file="${PATTON_LAB_OPENAI_KEY_FILE:-$HOME/whystuck/scratchpad/openai.key}"
unset PATTON_LAB_GROQ_KEY PATTON_LAB_OPENAI_KEY

cd "$here" || exit 4

if [ "$#" -eq 0 ]; then
    printf 'usage: %s <command> [options]\n' "$0" >&2
    exit 1
fi

command_name="$1"
needs=""
case "$command_name" in
    smoke)
        needs="groq"
        previous=""
        for arg in "$@"; do
            case "$arg" in
                --provider=*) needs="${arg#--provider=}" ;;
            esac
            [ "$previous" = "--provider" ] && needs="$arg"
            previous="$arg"
        done
        ;;
    record)
        if ! needs="$(python3 -m patton_lab.run providers "${@:2}")"; then
            printf 'could not work out which providers this command needs; nothing was run\n' >&2
            exit 4
        fi
        ;;
esac

load_key() {  # $1 variable name, $2 file
    if [ ! -r "$2" ]; then
        printf 'the %s key file is missing or unreadable; nothing was run\n' "$1" >&2
        exit 4
    fi
    export "$1=$(tr -d '\n\r ' < "$2")"
}
case " $needs " in *" groq "*) load_key PATTON_LAB_GROQ_KEY "$groq_file" ;; esac
case " $needs " in *" openai "*) load_key PATTON_LAB_OPENAI_KEY "$openai_file" ;; esac

reconnect=0
restore() {
    if [ "$reconnect" -eq 1 ]; then
        mullvad connect >/dev/null 2>&1
        for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
            [ "$(mullvad status 2>/dev/null | head -1)" = "Connected" ] && break
            sleep 1
        done
        if [ "$(mullvad status 2>/dev/null | head -1)" = "Connected" ]; then
            printf 'mullvad: reconnected\n' >&2
        else
            printf '!! mullvad did NOT reconnect; run `mullvad connect` now\n' >&2
        fi
    fi
}
trap restore EXIT
trap 'exit 130' INT TERM HUP

case " $needs " in
    *" groq "*)
        if command -v mullvad >/dev/null 2>&1 && [ "$(mullvad status 2>/dev/null | head -1)" = "Connected" ]; then
            reconnect=1
            mullvad disconnect >/dev/null 2>&1
            for _ in 1 2 3 4 5 6 7 8 9 10; do
                [ "$(mullvad status 2>/dev/null | head -1)" = "Disconnected" ] && break
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
