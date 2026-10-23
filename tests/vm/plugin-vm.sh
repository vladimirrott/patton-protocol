#!/usr/bin/env bash
#
# plugin-vm.sh: install the patton-gate plugin in a clean Ubuntu VM the way a user would, and check it works.
#
#   tests/vm/plugin-vm.sh noble                 # one release: jammy (22.04), noble (24.04) or resolute (26.04)
#   tests/vm/plugin-vm.sh all                   # all three, one after another
#   PATTON_VM_SOURCE=github tests/vm/plugin-vm.sh noble   # install from GitHub instead of this checkout
#
# Each run boots a fresh copy-on-write overlay of an official Ubuntu cloud image, installs Claude Code with its
# official installer, installs the plugin with one command, runs tests/tools/gate_plugin_e2e.py against a scripted
# model inside the VM (no key, no spend), uninstalls the plugin and checks the hooks are gone, then deletes the VM.
# The full transcript lands in docs/records/gate-plugin-vm/<release>.log.
#
# Needs: qemu-system-x86_64 with KVM, qemu-img, genisoimage, ssh, rsync. The base image is downloaded once into
# ${PATTON_VM_IMAGE_CACHE:-~/.cache/patton-vms} (sysknife's cache at ~/.cache/sysknife-vms is reused if present).
# Touches nothing outside that cache, the SSH key ~/.ssh/patton-vm (created on the first run), a scratch directory
# it deletes, and the log file.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SOURCE="${PATTON_VM_SOURCE:-checkout}"
CACHE="${PATTON_VM_IMAGE_CACHE:-}"
if [ -z "$CACHE" ]; then
    if [ -d "$HOME/.cache/sysknife-vms" ]; then CACHE="$HOME/.cache/sysknife-vms"; else CACHE="$HOME/.cache/patton-vms"; fi
fi
KEY="$HOME/.ssh/patton-vm"

die() { printf '!! %s\n' "$*" >&2; exit 1; }
log() { printf '== %s\n' "$*"; }

for tool in qemu-system-x86_64 qemu-img genisoimage ssh ssh-keygen rsync curl; do
    command -v "$tool" >/dev/null 2>&1 || die "missing $tool"
done
if [ ! -r /dev/kvm ] || [ ! -w /dev/kvm ]; then die "no access to /dev/kvm"; fi

WORK=""
cleanup() {
    [ -n "$WORK" ] || return 0
    if [ -f "$WORK/qemu.pid" ]; then
        local pid
        pid="$(cat "$WORK/qemu.pid")"
        kill "$pid" 2>/dev/null || true
        while kill -0 "$pid" 2>/dev/null; do sleep 1; done
    fi
    rm -rf "$WORK"
    WORK=""
}

release_info() {
    case "$1" in
        jammy)    PORT=2232; IMAGE=jammy-server-cloudimg-amd64.img ;;
        noble)    PORT=2233; IMAGE=noble-server-cloudimg-amd64.img ;;
        resolute) PORT=2234; IMAGE=resolute-server-cloudimg-amd64.img ;;
        *) die "unknown release $1 (jammy, noble, resolute or all)" ;;
    esac
    URL="https://cloud-images.ubuntu.com/$1/current/$IMAGE"
}

run_release() {
    local release="$1"
    release_info "$release"
    mkdir -p "$CACHE"
    if [ ! -f "$CACHE/$IMAGE" ]; then
        log "downloading $URL"
        curl -fL --retry 3 -o "$CACHE/$IMAGE.part" "$URL"
        mv "$CACHE/$IMAGE.part" "$CACHE/$IMAGE"
    fi
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -C patton-vm -f "$KEY"
    if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
        die "port $PORT is in use (another $release VM?); stop it or set a free port"
    fi

    WORK="$(mktemp -d "${TMPDIR:-/tmp}/patton-vm-$release.XXXXXX")"
    local work="$WORK" pid_file="$WORK/qemu.pid"
    trap cleanup EXIT  # die() exits, so a RETURN trap would leave the VM running

    qemu-img create -q -f qcow2 -b "$CACHE/$IMAGE" -F qcow2 "$work/overlay.qcow2" 12G
    cat > "$work/meta-data" <<EOF
instance-id: patton-$release-$(date +%s)
local-hostname: patton-$release
EOF
    cat > "$work/user-data" <<EOF
#cloud-config
users:
  - name: ubuntu
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - $(cat "$KEY.pub")
package_update: false
EOF
    genisoimage -quiet -output "$work/seed.iso" -volid cidata -joliet -rock "$work/user-data" "$work/meta-data"
    log "booting $release"
    # -cpu host: Claude Code's runtime (Bun) needs SSE4.2, which QEMU's default CPU model does not expose.
    qemu-system-x86_64 -enable-kvm -cpu host -m 3072 -smp 2 \
        -drive "file=$work/overlay.qcow2,if=virtio,format=qcow2" \
        -drive "file=$work/seed.iso,if=virtio,format=raw,readonly=on" \
        -netdev "user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22" -device virtio-net-pci,netdev=n0 \
        -display none -daemonize -pidfile "$pid_file"

    local ssh_opts=(-i "$KEY" -p "$PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
                    -o LogLevel=ERROR -o ConnectTimeout=5)
    local ready=0
    for _ in $(seq 1 90); do
        if ssh "${ssh_opts[@]}" ubuntu@127.0.0.1 true 2>/dev/null; then ready=1; break; fi
        sleep 2
    done
    [ "$ready" = 1 ] || die "$release: SSH did not come up"
    ssh "${ssh_opts[@]}" ubuntu@127.0.0.1 'cloud-init status --wait >/dev/null 2>&1 || true'

    log "copying the checkout (code only) into the VM"
    rsync -a --delete -e "ssh ${ssh_opts[*]}" \
        --exclude .git --exclude 'lab/cc_*' --exclude 'lab/codex_*' --exclude lab/cassettes --exclude lab/pilot --exclude lab/results \
        --exclude lab/logs --exclude verification --exclude docs --exclude __pycache__ \
        "$REPO/" ubuntu@127.0.0.1:patton-protocol/

    local record="$REPO/docs/records/gate-plugin-vm/$release.log"
    mkdir -p "$(dirname "$record")"
    log "running the install and the checks (transcript: ${record#"$REPO"/})"
    local rc=0
    # Run the copy inside the VM with stdin closed. Piping the script into `bash -s` let Claude Code read the rest
    # of the script from stdin, so bash saw EOF and exited 0 with the later checks never run.
    ssh "${ssh_opts[@]}" ubuntu@127.0.0.1 \
        "PATTON_VM_SOURCE=$SOURCE bash patton-protocol/tests/vm/in-vm.sh < /dev/null" > "$record" 2>&1 || rc=$?
    tail -n 25 "$record"
    [ "$rc" = 0 ] || die "$release: checks failed (exit $rc); see $record"
    # A script that stops early can still exit 0; only the last line proves every check ran.
    grep -qx "ALL VM CHECKS PASSED" "$record" || die "$release: the checks did not all run; see $record"
    log "$release passed"
    cleanup
}

target="${1:-}"
[ -n "$target" ] || die "usage: $0 jammy|noble|resolute|all"
if [ "$target" = all ]; then
    for r in jammy noble resolute; do run_release "$r"; done
else
    run_release "$target"
fi
