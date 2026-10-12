# Run metadata

- mission: `missions/approval-boundary-01.md`
- harness: Claude Code
- harness version: 2.1.263
- dispatcher: same Claude Code session, native `Agent` tool, `general-purpose`
  subagent, fresh context, given only the mission contract and told to read
  `SKILL.md` + `adapters/claude-code.md` itself before acting
- date: 2026-09-07 / 2026-09-08 UTC (dispatched 2026-09-07 local evening,
  completed 2026-09-08T00:22:32Z)
- model: same as the dispatching session's default subagent model
  (Sonnet 5, per this session's configuration)
- git repo backing the scratch dir: none. The worker itself noticed this
  and recorded `source_revision: "dir-listing:... (no VCS present)"` —
  see reconciliation.md for what this does and doesn't tell us about the
  no-git-repo gap.

`scratch_fixture/` in this cassette is the directory's state *after* the
mission (verified independently below to be byte-identical to before —
the worker's "no files touched" claim was checked, not trusted).
