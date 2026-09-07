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

## Install

Copy this directory into wherever your host discovers Agent Skills packages
(a project or user skills directory, for example), or point your host at it
directly if it supports explicit skill invocation by path. No build step, and
no runtime dependency beyond the host itself.

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

## Contributing

Bug reports, new host adapters, and protocol proposals are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for how this repository is organized, the
test-driven workflow it expects, and what a new adapter needs. This project
follows the [Contributor Covenant](CODE_OF_CONDUCT.md). Security issues in
the validator should go through the process in [SECURITY.md](SECURITY.md)
rather than a public issue.

## License

[MIT](LICENSE)
