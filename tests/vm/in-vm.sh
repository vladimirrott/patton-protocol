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
listing="$(claude plugin list)"
printf '%s\n' "$listing"
# The README tells a user to check the install this way.
grep -q "patton-gate@patton-protocol" <<<"$listing" && grep -q "enabled" <<<"$listing" \
    || { echo "!! claude plugin list does not show patton-gate@patton-protocol enabled"; exit 1; }

step "the gate, end to end, in three permission modes"
python3 "$HOME/patton-protocol/tests/tools/gate_plugin_e2e.py" --claude "$(command -v claude)" --installed

step "the approval prompt, as a person sees it (interactive Claude Code in tmux)"
command -v tmux >/dev/null 2>&1 || sudo apt-get install -y -qq tmux >/dev/null
python3 "$HOME/patton-protocol/tests/tools/gate_plugin_tui.py" --claude "$(command -v claude)" --installed

step "what the gate recorded, with the README's command as written"
data_dir="$HOME/.claude/plugins/data/patton-gate-patton-protocol"
[ -d "$data_dir/sessions" ] || { echo "!! no ledgers in $data_dir, where the README says they are"; exit 1; }
# Unquoted on purpose: the README's glob over the installed version, expanded as a user's shell expands it.
recorded="$(python3 ~/.claude/plugins/cache/patton-protocol/patton-gate/*/scripts/patton_gate.py log)"
printf '%s\n' "$recorded"
grep -q "^Patton gate .*, session " <<<"$recorded" || { echo "!! the README's log command shows no session"; exit 1; }

e2e() { python3 "$HOME/patton-protocol/tests/tools/gate_plugin_e2e.py" --claude "$(command -v claude)" --installed \
        --modes bypassPermissions > /tmp/e2e.txt 2>&1; }
expect_no_gate() {
    if e2e; then cat /tmp/e2e.txt; echo "!! the gate still ran $1"; exit 1; fi
    grep -q "no hook ran" /tmp/e2e.txt || { cat /tmp/e2e.txt; echo "!! unexpected failure $1"; exit 1; }
    echo "$1: no hook ran, as expected"
}

step "disable, then enable, from inside a project as a user would"
project="$(mktemp -d)"
git -C "$project" init -q 2>/dev/null || true
toggled="$(cd "$project" && claude plugin disable patton-gate@patton-protocol)"
printf '%s\n' "$toggled"
grep -q "scope: user" <<<"$toggled" || { echo "!! disable inside a project did not act on the user scope"; exit 1; }
expect_no_gate "after disable"
toggled="$(cd "$project" && claude plugin enable patton-gate@patton-protocol)"
printf '%s\n' "$toggled"
grep -q "scope: user" <<<"$toggled" || { echo "!! enable inside a project did not act on the user scope"; exit 1; }
[ -z "$(find "$project" -path "$project/.git" -prune -o -type f -print)" ] \
    || { echo "!! disable or enable wrote into the project"; exit 1; }
e2e ||{ cat /tmp/e2e.txt; echo "!! the gate did not come back after enable"; exit 1; }
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
