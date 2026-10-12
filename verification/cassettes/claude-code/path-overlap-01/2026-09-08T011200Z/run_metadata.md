# Run metadata

- mission: `missions/path-overlap-01.md`
- harness: Claude Code 2.1.263
- date: 2026-09-08 UTC
- dispatcher: native `Agent` tool, two `general-purpose` subagents
  dispatched in the **same message** (genuinely concurrent, not
  sequential) — this is the mechanism Claude Code itself documents for
  parallel dispatch, deliberately misused here (overlapping
  `allowed_paths`) as a red-team of the "0 mutable-file overlap" rule
- initial fixture: `shared.txt` = `"shared counter file\nvisits: 0\n"`
- final fixture state: `final_fixture_state/shared.txt` (see below)
