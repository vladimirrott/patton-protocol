# Verification: does Patton Protocol actually work?

`scripts/validate_protocol.py` and `tests/` prove the *documents* are
internally consistent — every required lifecycle term is present, every
adapter capability table is well-formed, every cross-reference resolves.
None of that proves the *protocol* is followable: that a real AI coding
agent, given only `SKILL.md` and `references/`, can actually run a mission
through Scope → Plan → Dispatch → Observe → Reconcile → Verify and produce
the safety properties the spec claims (bounded workers, an independent
verifier that doesn't rubber-stamp, an approval boundary that isn't
bypassable).

This directory is that second kind of proof: real missions, dispatched to
real agent harnesses, with the full transcript and artifacts kept as
evidence.

## Layout

- `missions/` — mission-contract fixtures, each one a fixed, reusable
  scenario (objective, `allowed_paths`, budget/timeout/retry limits) run
  identically across harnesses. A mission fixture is written once and never
  edited after its first use; a changed scenario gets a new mission id.
- `cassettes/<harness>/<mission-id>/<run-timestamp>/` — the recorded
  evidence for one trial: every artifact the worker(s) produced, the
  independent verifier's report (verbatim), the reconciliation notes, and a
  `manifest.sha256` covering everything else in that directory.
- `log.md` — dated, append-only, newest entry first. One entry per trial:
  harness + pinned version, mission id, verdict, link to its cassette.
- `verify_cassettes.py` — recomputes every cassette's hashes and fails
  loudly on any mismatch.

## What "cassette" means here (and how it differs from whystuck's)

`whystuck/src/whystuck/eval/cassette.py` records LLM call outputs keyed by
a hash of `(name, args, kwargs)` so a later run can *replay* them: same
input, same output, zero tokens spent, byte-identical every time. That
works there because the calls are single `prompt -> str` functions.

An agent-harness mission is not that. Dispatching the same mission contract
to Claude Code twice will not produce byte-identical transcripts — model
sampling, tool-call ordering, and timing all vary. So a cassette here is not
byte-for-byte replayable the way whystuck's is; it is an **evidence
record**, not a replay substitute. What's reproducible is the *mission*
(the exact contract, versioned and never edited after first use) and the
*procedure* (pinned harness version, documented commands), not the model's
output token-for-token.

## Tamper-evidence, and what it does and doesn't guarantee

Every cassette directory gets a `manifest.sha256` in standard `sha256sum`
format (`<hex digest>  <relative path>` per line, one per artifact file),
generated and checked by `verify_cassettes.py`. `log.md` records, per
trial, the sha256 of that trial's own `manifest.sha256` — a hash of the
hash list, so a single line in the dated log attests to every byte of that
whole cassette. Changing one character in any recorded artifact changes
its file hash, which changes the manifest, which changes the digest
recorded in `log.md` — any of the three lets a reader detect the tamper
by recomputing and comparing.

Committing each cassette to git adds a second, independent trail: the
commit that first introduced a cassette fixes its blob hashes in history,
so a later edit shows as a diff no matter how the working tree is
inspected.

**What this does not guarantee:** nothing here proves the transcript was
honestly captured *before* hashing — a dishonest recorder could hash a
fabricated transcript just as validly as a real one. The hash chain
detects **post-hoc tampering with a recorded artifact**, not fabrication
at recording time. That's why every mission in this test matrix is run and
reconciled by an agent with actual read/execute access to the artifacts
(reruns the code, re-derives the verdict), not by trusting a pasted
transcript on faith — the independent-verification step is what carries
the honesty burden; the hash chain only carries the non-tampering burden
after that.

`python3 verification/verify_cassettes.py` runs in CI and fails when:

- a recorded artifact no longer matches its manifest;
- a trial directory (found by its `run_metadata.md`) has no manifest;
- `cassettes/` holds no trial at all, because a run over zero cassettes
  checked nothing;
- a manifest's digest is absent from `log.md`. An editor who changes an
  artifact and regenerates its manifest still fails here, because the new
  digest was never logged.

An editor who rewrites the artifact, the manifest, and the log entry passes
the script. That edit shows up as a change to `log.md` in git history, so
reviewers should read every change to that file.

## Limits of this evidence

Every trial below has one run per cell (Pi has two, and they disagreed), and
none has a baseline arm that gives the worker the same mission without the
protocol. A trial records what one worker did once. It does not show that
the protocol caused the behavior. The 2026-10-12 entry in `log.md` also
records a conflict between the `approval-boundary-01` pass condition and the
text of `SKILL.md`.

Transcripts were redacted before publication: the recording machine's home
directory and user name became `user`. The same entry lists what changed.

## Harnesses in the test matrix, pinned versions

| Harness | Version pinned | Cassette on record? | Install |
|---|---|---|---|
| Claude Code | 2.1.263 | yes — `approval-boundary-01`, `budget-boundary-01`, `builder-baseline-01`, `digest-lock-01`, `fixit-temptation-01`, `path-overlap-01` | already installed |
| Codex CLI | 0.153.4 | yes — `approval-boundary-01` | already installed (`npm i -g @openai/codex`) |
| Pi (`@mariozechner/pi-coding-agent`) | 0.73.1 | yes — `approval-boundary-01` | `npm i -g @mariozechner/pi-coding-agent@0.73.1`; Groq via `GROQ_API_KEY` env var, `--provider groq --model openai/gpt-oss-120b` |
| OpenCode | 1.18.29 | attempted, inconclusive — see `cassettes/opencode/approval-boundary-01/` | `npm i -g opencode-ai@1.18.29`; Groq via `GROQ_API_KEY` env var, model `groq/openai/gpt-oss-120b` |

Versions pinned as of 2026-09-07 (confirmed via `npm view <pkg> version`
and each CLI's own `--version`). A harness upgrade after that date does not
retroactively change a past trial's recorded version; a re-run against a
newer version is a new trial with its own log entry and its own pinned
version, never an edit to the old one.

**Naming note on Pi:** the npm package installed and pinned above,
`@mariozechner/pi-coding-agent@0.73.1`, prints a deprecation notice on
install pointing at `@earendil-works/pi-coding-agent` as its replacement
going forward (same for its `pi-agent-core`/`pi-tui`/`pi-ai` dependencies).
The `@mariozechner` scope is what's pinned here because it's what version
0.73.1 actually is; a future trial against the `@earendil-works` package
is a different install and gets its own version row and its own cassette,
not a silent substitution under the same name.

Groq model pinned to `openai/gpt-oss-120b` (Groq-hosted, 131K context,
tool-use capable) for every harness's Groq-backed trial, so a cross-harness
comparison isn't also silently a cross-model comparison. `GROQ_API_KEY` is
read from `~/.groq-staging-key`; Mullvad must be disconnected first — Groq
blocks Mullvad exit IPs at the edge (403, reads like a bad key but isn't
one).

## Reproducing a trial

1. Confirm the pinned harness version matches the table above (or the
   trial's own `log.md` entry, which is authoritative if they ever drift).
2. Read the mission fixture in `missions/<mission-id>.md` verbatim; do not
   paraphrase it when dispatching, since a paraphrase is a different trial.
3. Dispatch it exactly as written to the target harness.
4. Have a **different** agent instance (fresh context, ideally a different
   harness or at minimum a different session) independently reconcile: it
   must re-derive the verdict from the artifacts, not trust the worker's
   own report.
5. Record everything under a new `cassettes/<harness>/<mission-id>/<UTC
   timestamp>/` directory, run `verify_cassettes.py` to generate its
   manifest, and add the dated entry to `log.md`.

## Adding a harness

Add a row to the version table above with the exact pinned version and
install command, then run every existing mission fixture against it,
recording each as its own cassette. A harness enters the matrix only once
at least one mission has actually been run against it and recorded —
listing it in the table without a cassette is not a supported claim.
