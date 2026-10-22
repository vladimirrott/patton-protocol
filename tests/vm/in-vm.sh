#!/usr/bin/env bash
# Runs inside a fresh Ubuntu VM (tests/vm/plugin-vm.sh sends it over SSH). Installs Claude Code and the plugin the
# way the README says, checks the gate with a scripted model, uninstalls, and checks the hooks are gone.
set -euo pipefail
step() { printf '\n=== %s\n' "$*"; }
when() { date +%s; }

step "system"
. /etc/os-release && echo "$PRETTY_NAME"
python3 --version

step "install Claude Code with the official installer"
t0=$(when)
curl -fsSL https://claude.ai/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
claude --version
echo "installer took $(( $(when) - t0 )) s"

step "install the plugin with one command (source: ${PATTON_VM_SOURCE:-checkout})"
if [ "${PATTON_VM_SOURCE:-checkout}" = github ]; then
    market="vladimirrott/patton-protocol"
else
    market="$HOME/patton-protocol"
fi
t0=$(when)
claude plugin install patton-gate --marketplace "$market" -y
echo "plugin install took $(( $(when) - t0 )) s"
claude plugin list

step "the gate, end to end, in three permission modes"
python3 "$HOME/patton-protocol/tests/tools/gate_plugin_e2e.py" --claude "$(command -v claude)" --installed

step "the approval prompt, as a person sees it (interactive Claude Code in tmux)"
command -v tmux >/dev/null 2>&1 || sudo apt-get install -y -qq tmux >/dev/null
python3 "$HOME/patton-protocol/tests/tools/gate_plugin_tui.py" --claude "$(command -v claude)" --installed

step "what the gate recorded"
gate_py="$(find "$HOME/.claude/plugins" -path '*patton-gate*' -name patton_gate.py | head -1)"
data_dir="$(find "$HOME/.claude/plugins/data" -maxdepth 1 -name 'patton-gate*' | head -1)"
[ -n "$gate_py" ] && [ -n "$data_dir" ] || { echo "!! installed gate or its data directory not found"; exit 1; }
echo "installed at ${gate_py%/scripts/patton_gate.py}, data in $data_dir"
CLAUDE_PLUGIN_DATA="$data_dir" python3 "$gate_py" log

e2e() { python3 "$HOME/patton-protocol/tests/tools/gate_plugin_e2e.py" --claude "$(command -v claude)" --installed \
        --modes bypassPermissions > /tmp/e2e.txt 2>&1; }
expect_no_gate() {
    if e2e; then cat /tmp/e2e.txt; echo "!! the gate still ran $1"; exit 1; fi
    grep -q "no hook ran" /tmp/e2e.txt || { cat /tmp/e2e.txt; echo "!! unexpected failure $1"; exit 1; }
    echo "$1: no hook ran, as expected"
}

step "disable, then enable"
claude plugin disable patton-gate@patton-protocol
expect_no_gate "after disable"
claude plugin enable patton-gate@patton-protocol
e2e || { cat /tmp/e2e.txt; echo "!! the gate did not come back after enable"; exit 1; }
echo "after enable: the gate runs again"

step "uninstall with --keep-data, and check the hooks are gone and the ledgers stay"
claude plugin uninstall patton-gate@patton-protocol --keep-data -y
claude plugin list
expect_no_gate "after uninstall"
[ -d "$data_dir/sessions" ] && echo "ledgers kept: $(ls "$data_dir/sessions" | wc -l) session(s) in $data_dir" \
    || { echo "!! --keep-data did not keep $data_dir"; exit 1; }
claude plugin marketplace remove patton-protocol
claude plugin marketplace list
echo "ALL VM CHECKS PASSED"
