# Contributing to Patton Protocol

Thanks for considering a contribution. This project has no build step and no
third-party dependencies, so getting set up takes about a minute.

## Setup

Clone the repository and confirm the suite passes:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_protocol.py .
```

Both commands need only Python 3.11+ from the standard library.

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
