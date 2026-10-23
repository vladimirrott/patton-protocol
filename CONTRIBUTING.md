# Contributing to Patton Protocol

Thanks for considering a contribution. This project has no build step and no
third-party dependencies, so getting set up takes about a minute.

## Setup

Clone the repository and confirm the suite passes:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_protocol.py .
```

Both commands need only Python 3.11+ from the standard library. The gate plugin itself needs 3.10+; the Ubuntu
22.04 VM run below checks it on 3.10. Run every command in this file from the repository root.

## How this repository is organized

- **`SKILL.md`** is the canonical, normative entrypoint. If a change affects
  behavior, it starts here.
- **`references/`** holds the mission contract, worker-report contract, and
  safety/budget rules that `SKILL.md` links to.
- **`adapters/`** documents how each host (Claude Code, Codex, Cursor) maps
  the portable capabilities to its own surfaces. Adapters are explanatory:
  they describe what a host can do, they never become a dependency of the
  portable core.
- **`scripts/validate_protocol.py`** is a dependency-free structural
  validator for the package (frontmatter, links, required terms, vendor-syntax
  boundary, adapter capability labels, worker-report schema).
- **`scripts/validate_report.py`** checks that a worker report carries the fields
  `references/worker-report.md` requires. The lab uses it to count schema-valid
  reports.
- **`lab/`** is the replayable study of what the protocol changes. The recorded
  results ran the exact texts of `SKILL.md`, `references/`, `adapters/claude-code.md`
  and `lab/arms/`, so editing any of them fails the lab's pre-registration check
  and the replay test (`tests/test_lab_study_integrity.py`). Measuring a protocol
  change needs a new pre-registration, not an edit to the old one.
- **`plugins/patton-gate/`** is the gate people install. Its code is one file,
  `plugins/patton-gate/scripts/patton_gate.py`; change the gate there. **`gate/patton_gate.py`** is the version
  the 2026-10-21 study froze and must never change (the pre-registration and the replay hash it). See
  "Working on the gate" below.
- **`lab/patton_lab/cc/`** and **`lab/patton_lab/codex/`** run the gate study inside Claude Code and inside Codex
  CLI. Both studies are pre-registered, and each pre-registration hashes the harness files it depends on: the
  Codex one covers `gate/patton_gate.py`, `lab/patton_lab/codex/*.py`, the `lab/patton_lab/cc/` files it imports
  and the model catalog copy in `lab/codex/catalog/`. Before you commit a change in `lab/`, run
  `python3 -m patton_lab.cc.prereg --check` and `python3 -m patton_lab.codex.prereg --check` from `lab/`; a
  failure means the change would alter a frozen study, so put it in a new module instead. The tests are
  `tests/test_cc_*.py` and `tests/test_codex_*.py`; `tests/test_codex_contract.py` needs Codex CLI 0.159.0 and
  skips without it.
- **`tests/test_validate_protocol.py`** enforces the contract in `SKILL.md`
  and `references/` at the wording level: it is the actual spec enforcement,
  not incidental coverage. Expect it to be strict about exact phrasing in a
  few places (particularly the adapter approval-boundary language); that
  strictness is deliberate, not accidental.

## Test-driven development is the expected workflow here

This project was built test-first, and changes should keep that discipline:

1. Write a failing test (or a new fixture under `tests/fixtures/`) that
   captures the behavior you want.
2. Run the suite and confirm it fails for the reason you expect.
3. Write the minimal change that makes it pass.
4. Run the suite and the validator again.
5. Commit.

If you add a fixture meant to fail a specific check, verify it actually fails
for that reason, not for an unrelated one; a fixture's own descriptive prose
can accidentally satisfy the very check it's supposed to violate (this bit
the project once already: see the git history around
`tests/fixtures/invalid-report-schema/`).

## The candidate-manifest rules are deliberately repeated, not shared

`SKILL.md`'s Plan section, `references/mission-contract.md`, and
`references/safety-and-budgets.md` each restate the untracked/candidate-digest
rules (symlink and gitlink rejection, the candidate/non-candidate path
partition, post-lock reclassification) in near-identical prose. That's
intentional, each file's tests substring-match the rule independently in
that file's own context, but it means **no test enforces that the three
copies stay semantically equivalent**. If you correct one of these rules,
update all three, and read the other two closely rather than assuming a grep
for the same phrase will catch every copy; the wording differs slightly
between files on purpose.

## Adding a fourth host adapter

`adapters/claude-code.md`, `adapters/codex.md`, and `adapters/cursor.md` are
the closest thing to a template. A new adapter needs:

- A `## Capability matrix` section with exactly one row per capability listed
  in `scripts/validate_protocol.py`'s `ADAPTER_REQUIRED_CAPABILITIES`, each
  row's status one of `native`, `prompt-mediated`, or `unavailable`.
- Prose stating the human-approval boundary and the nested-worker-spawn
  boundary for that host, in enough detail that `adapter_document_errors`
  and `validate_protocol.adapter_capability_errors` both accept it.
- A link from `references/harness-adapters.md`'s host-notes list.

Run `python3 scripts/validate_protocol.py .` before opening a PR; it will
tell you exactly which required piece is missing.

## Working on the gate

Change the gate in `plugins/patton-gate/scripts/patton_gate.py`. Never edit `gate/patton_gate.py`: it is the
file the 2026-10-21 study froze, and the pre-registration check and the replay fail on any change to it.

The plugin's tests run with the rest of the suite and need nothing beyond Python (one test also runs
`claude plugin validate` when `claude` is installed, and is skipped otherwise):

```sh
python3 -m unittest tests.test_gate_plugin tests.test_gate_corpus
```

`tests/test_gate_corpus.py` runs the bypass corpus against the studied gate, and `tests/test_gate_plugin.py` runs
it again against the plugin's copy, so a classifier change has to keep every case. A new way to reach a file belongs in the corpus first: add
the case, watch it fail, then change the classifier.

To try a change in a real session without installing anything:

```sh
claude --plugin-dir plugins/patton-gate
```

Two scripts drive real Claude Code with the plugin against a scripted model, for $0 and with no key. They need
`claude` 2.1.295 or newer on `PATH`, and the second needs `tmux`. Each run uses a scratch project and a scratch
home, so your own Claude Code settings stay untouched:

```sh
python3 tests/tools/gate_plugin_e2e.py --claude claude --plugin-dir plugins/patton-gate   # three permission modes
python3 tests/tools/gate_plugin_tui.py --claude claude --plugin-dir plugins/patton-gate   # the prompt a person sees
```

Both end with `all checks passed` or a list of failures. `--installed` tests the plugin installed for your user
instead; it uses your real home and marks the scratch project as trusted in `~/.claude.json`, so the VM script uses
it and you need not. To read what the gate recorded in your own `--plugin-dir` sessions, run
`python3 plugins/patton-gate/scripts/patton_gate.py log`.

`claude plugin validate ./plugins/patton-gate --strict` checks the manifest and the hooks.

To check the whole install in a clean Ubuntu VM, the way a user installs it:

```sh
tests/vm/plugin-vm.sh noble        # jammy (22.04), noble (24.04), resolute (26.04), or all
```

It needs an x86_64 Linux host with read and write access to `/dev/kvm`, `qemu-system-x86_64`, `qemu-img`,
`genisoimage`, `ssh`, `ssh-keygen`, `rsync`, `curl` and internet access. Each VM takes 3 GB of RAM, 2 CPUs and host
port 2232, 2233 or 2234 (the script refuses a busy port). The first run downloads the cloud image (630 to 860 MB
per release) and creates the SSH key `~/.ssh/patton-vm`; later runs reuse both. One release took about seven
minutes on 2026-10-22, most of it in the interactive checks.

It boots a throwaway VM, installs Claude Code with the official installer, installs the plugin with
`claude plugin install patton-gate --marketplace <source> -y`, runs both scripts above inside it, runs the README's
`log` command as written, checks `disable` and `enable` from inside a project, `uninstall --keep-data` and
`marketplace remove`, and deletes the VM. By default the source is your checkout; `PATTON_VM_SOURCE=github`
installs from GitHub, which is the user's path, so run that after a push. A run passes only when the transcript
ends with `ALL VM CHECKS PASSED`. It overwrites the tracked transcript `docs/records/gate-plugin-vm/<release>.log`:
commit it when the run tested what you are shipping, and restore it otherwise. QEMU must pass `-cpu host` (the
script does): Claude Code needs SSE4.2.

To release a new version, change `VERSION` in `plugins/patton-gate/scripts/patton_gate.py`, `version` in
`plugins/patton-gate/.claude-plugin/plugin.json`, and the plugin's `version` in `.claude-plugin/marketplace.json`
together; a test fails when they differ.

## Opening a pull request

- Keep the PR scoped to one change. `SKILL.md` and `references/` are
  precisely worded on purpose; a PR that reads as several unrelated edits is
  harder to review carefully.
- Include the test or fixture that proves the change, not just the change
  itself.
- Describe *why*, not just *what*, especially for anything touching the
  approval-boundary or candidate-identity rules; those exist to close a
  specific safety gap, and a reviewer needs to know which gap your change
  affects.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## Questions

Open an issue. There's no separate mailing list or chat for this project yet.
