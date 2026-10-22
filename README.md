<p align="center">
  <img src="assets/logo.svg" width="160" alt="Patton Protocol mark: an olive star with three red arrows branching outward">
</p>

# Patton Protocol

![CI](https://github.com/vladimirrott/patton-protocol/actions/workflows/ci.yml/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Patton Protocol is a portable Agent Skills package for coordinating bounded
coding-agent missions. The canonical entrypoint is [`SKILL.md`](SKILL.md); it
alone is normative. Everything else here, the reference contracts, the host
adapter notes, the validator, and this README, exists to support that one
file, never to replace it.

Any Agent Skills-compatible host can load this package in principle. The
[host adapter notes](references/harness-adapters.md) and the per-host files
under [`adapters/`](adapters/) document how Claude Code, Codex, and Cursor
currently expose the protocol's capabilities, capability by capability, using
the `native`, `prompt-mediated`, and `unavailable` vocabulary defined there.
These notes describe what a host can do today; they are not a support
whitelist, and a host absent from them is not excluded, its capabilities are
simply unverified. Runtime worker spawning, in particular, stays host-dependent:
some hosts can start real, isolated workers, others cannot, and the protocol
requires neither.

When a host cannot spawn workers at all, Patton Prime falls back to serial
execution. Serial fallback is not a degraded mode with weaker rules: it is
the same protocol, the same mission and report contracts, the same evidence
checks, and the same approval gates, running one bounded mission at a time in
the main loop instead of several in parallel.

## What the evidence shows

Two pre-registered studies measured this package against baselines. Read them before you rely on the
protocol text to keep an agent from destroying files.

- **Protocol text.** With the full `SKILL.md` in its prompt, a worker harmed 0.413 of cleanup runs,
  against 0.325 for a generic prompt ([lab findings](docs/findings/2026-10-13-lab-findings.md)). In a
  real Claude Code session the installed skill was offered in 224 runs, and the worker never invoked it.
- **The gate.** [`gate/patton_gate.py`](gate/README.md) is a Claude Code PreToolUse hook that asks
  before any file that existed at session start changes. Over 560 runs of Claude Code on gpt-oss-120b,
  unguarded workers harmed 0.196 of cleanup runs. With the hook and a simulated operator, harm was 0.000,
  0 of 224 gated runs got around the gate, and the hook asked 1.134 questions per run
  ([report](lab/CC_REPORT.md), [findings](docs/findings/2026-10-22-gate-study-findings.md)).
- **Open.** Whether the hook costs finished work (utility 0.862 unguarded against 0.768, inconclusive
  against the registered margin), and every result on a Claude model.

The [study record](docs/records/2026-10-22-gate-study-record.md) lists every check, review, deviation and
mistake behind these numbers, with the raw logs.

## Install

Copy `SKILL.md`, `references/`, `adapters/`, and `agents/` into wherever your host
discovers Agent Skills packages (a project or user skills directory, for example),
or point your host at this directory if it supports explicit skill invocation by
path. `lab/`, `verification/`, `scripts/`, and `tests/` support development and
need no installation. No build step, and no runtime dependency beyond the host
itself.

## Use

Invoke the skill (by name, `patton-protocol`, or however your host triggers a
skill) when a task genuinely splits into independent, boundable pieces. Patton
Prime decides whether delegation is worth it; a task that doesn't clear the
delegation threshold runs as one mission in the main loop, and that is a
correct outcome, not a failure to delegate.

Read, in order:

1. [`SKILL.md`](SKILL.md), for the lifecycle, roles, and limits.
2. [`references/mission-contract.md`](references/mission-contract.md) and
   [`references/worker-report.md`](references/worker-report.md), for the data
   exchanged with every worker.
3. [`references/safety-and-budgets.md`](references/safety-and-budgets.md),
   for the limits Prime enforces and how it recovers from failure or
   disagreement.
4. [`references/harness-adapters.md`](references/harness-adapters.md) and the
   `adapters/` notes, only when you need to know what a specific host can do.

## Validate

`scripts/validate_protocol.py` checks the package's structure with no
third-party dependencies:

```
python3 scripts/validate_protocol.py .
```

It confirms the canonical frontmatter, the required lifecycle and
orchestration terms, every relative link in the package, the worker-report
field schema, and each adapter's capability matrix. It exits `0` and prints
`valid` when the package is well-formed, and exits nonzero with one
diagnostic per problem otherwise.

## Test

```
python3 -m unittest discover -s tests -v
```

## Evidence

[`lab/`](lab/README.md) holds a pre-registered study with baseline arms. It ran 1344
jobs on two models, gpt-oss-120b on Groq and gpt-4.1-mini on OpenAI, and replays
offline for $0. [`lab/REPORT.md`](lab/REPORT.md) gives every prediction its verdict,
and the [findings](docs/findings/2026-10-13-lab-findings.md) read the results.

The main result limits what the protocol can claim. With the full `SKILL.md` in
its prompt, a worker harmed 0.413 of cleanup runs, against 0.325 for a generic
prompt (difference -0.088, 90% CI [-0.225, 0.050]). A 1.7 KB distillation cut
harm to 0.125 and cost utility (0.446, against 0.710 for the full text of 14.3 KB). A gate in
the sandbox that denies unapproved changes to existing files cut harm to 0.000
over 80 runs and kept utility at 0.746. Enforce the approval rules in the host: the
full text alone did not make these two models ask before deleting.

A second pre-registered study put the gate inside a real Claude Code session. The gate is
[`gate/patton_gate.py`](gate/README.md), a PreToolUse hook that asks before a file that
existed at session start changes. The study ran 560 runs of Claude Code 2.1.303 on gpt-oss-120b,
with every model call recorded in a cassette
([report](lab/CC_REPORT.md), [findings](docs/findings/2026-10-22-gate-study-findings.md)).
Unguarded (`bypassPermissions`, no hook), workers harmed 0.196 of cleanup runs. With the hook
and a simulated operator that approves only removals that lose nothing, harm was 0.000, and
no run got around the gate (0 of 224). The hook asked 1.134 times per run. Utility fell
from 0.862 to 0.768, and the data leave open whether that loss is under the registered 0.10
margin. The installed skill was offered in 224 runs and invoked in none. The Claude baseline
is still unmeasured.

[`verification/`](verification/README.md) holds earlier recorded runs of real agent
harnesses against the protocol, each with a tamper-evident manifest. Every trial
has one run per cell and no baseline arm, so the records show what one worker did
and leave open whether the protocol caused it. The lab supplies the baseline. The
directory's README lists the limits.

## Contributing

Bug reports, new host adapters, and protocol proposals are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for how this repository is organized, the
test-driven workflow it expects, and what a new adapter needs. This project
follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Security issues in
the validator should go through the process in [SECURITY.md](SECURITY.md)
rather than a public issue.

## License

[MIT](LICENSE)
