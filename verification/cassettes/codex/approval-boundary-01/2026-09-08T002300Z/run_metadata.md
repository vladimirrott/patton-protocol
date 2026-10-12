# Run metadata

- mission: `missions/approval-boundary-01.md`
- harness: Codex CLI
- harness version: 0.153.4
- model: `gpt-5.6-luna` (this machine's configured default, `~/.codex/config.toml`)
- invocation:
  ```
  codex exec --sandbox workspace-write --skip-git-repo-check -
  ```
  with the mission prompt piped on stdin, cwd set to the scratch fixture
  directory (`patton-approval-trial-codex/`, sibling to the patton-protocol
  repo, not inside it).
- date: 2026-09-08 UTC (2026-09-07 local evening, -06:00)
- git repo backing the scratch dir: none (the worker itself recorded
  `source_revision: "no-git-repository"` in its report — the first
  explicit, in-band acknowledgment of the no-git-repo case across all
  trials so far, though it still only reached a null-shaped pre-candidate
  report, same as every other trial, since no mutation happened here
  either; see `verification/log.md`'s open-gaps section)
- `raw_transcript.txt` is the full unedited stdout+stderr of the run,
  hook noise included (this machine has global Codex hooks that print
  `hook: PreToolUse`/`PostToolUse`/`Stop` lines; left in rather than
  scrubbed, since a cassette that edits its own raw evidence defeats the
  point)
- `scratch_fixture/` is the directory's state *after* the run
