# Harness Adapters

Patton Protocol has one portable core. These notes map its named capabilities
to host surfaces without changing the mission, report, evidence, or approval
contracts. Capability statuses are `native`, `prompt-mediated`, or
`unavailable`. The adapter status vocabulary is:

| Status | Meaning |
| --- | --- |
| `native` | The host supplies a documented surface for the capability. Prime still applies the protocol contract and records the result. |
| `prompt-mediated` | The host has no guaranteed primitive, so Prime asks the host or user to perform the action and records the response. |
| `unavailable` | The host cannot safely provide the capability. Prime uses the serial fallback or leaves the mission blocked. |

The canonical [SKILL.md](../SKILL.md) remains the only required entrypoint.
Adapter notes are explanatory and do not make a host command, plugin, account,
or permission model a dependency of the portable core.
The portable core does not require adapter dependencies.

## Capability mapping

| Capability | Claude Code | Codex | Cursor |
| --- | --- | --- | --- |
| Loading | `native` skill discovery or explicit invocation | `native` skill discovery or explicit invocation | `native` Agent Skills discovery or explicit invocation |
| Worker definition | `native` custom subagents or agent teams | `native` custom agents and skills | `native` plugin agents and skills |
| Parallel dispatch | `native` subagents or agent teams when enabled | `native` multi-agent support when enabled | `native` subagents or plugin agents when enabled; serial fallback when unavailable |
| Serial fallback | `prompt-mediated` main-loop missions | `prompt-mediated` main-loop missions | `prompt-mediated` main-loop missions |
| Approval boundary | `prompt-mediated` explicit human decision | `prompt-mediated` explicit human decision | `prompt-mediated` explicit human decision |
| Nested worker spawn | `native` for proven Claude Code 2.1.219 and inherited 2.1.263; unknown or version-unproven surfaces are `unavailable` with serial fallback | `native` when documented and enabled; V2 cap unproven requires serial fallback | `native` for root, direct subagents, and grandchildren; no great-grandchild spawn |

The table describes capability surfaces, not a support whitelist. An
Agent-Skills-compatible host can load the package in principle. Prime checks the
actual host capability matrix at dispatch time, records the selected status,
and preserves serial fallback when parallel dispatch is unavailable. A host
surface that can only coordinate dispatch through prompts is
`prompt-mediated`.

## Shared adapter rules

Loading imports the canonical `SKILL.md` and its relative references. Worker
definition maps one host worker to one mission identity and immutable Builder
ownership boundary. Parallel dispatch is valid only for independent missions
with disjoint mutable paths. A host that cannot provide that isolation runs
missions in serial order.

Serial fallback keeps the same mission and worker-report contracts, candidate
identity checks, evidence rechecking, timeout and budget controls, and
independent read-only Verifier. Prime assigns distinct Builder and Verifier
actor identities. If the host cannot provide a distinct Verifier actor, a
human performs verification; a same-actor result remains `blocked` and
`unverified`.

Approval boundaries stay outside worker authority. Workers, Verifiers, and
Quartermasters return evidence and leads. Before a merge, deployment,
deletion, publication, credential change, or other irreversible action, Prime
records one explicit human approver, the exact scope, the `approved` decision,
and the verified evidence. A host permission prompt or automated host-only
approval does not satisfy that gate. Denied or missing approval leaves the
mission blocked.

Nested spawning differs by host configuration, tool availability, depth limits,
and session policy. The Claude Code 2.1.219 changelog documents recursive
subagent spawn, with default depth 3 and a host environment override. Claude
Code 2.1.263 inherits that capability. Agent teams can dispatch workers, but
their current surface does not add recursive nesting proof. Unknown or
version-unproven Claude surfaces remain unavailable with serial fallback for an
unknown version. A fork with an explicit recursive-spawn implementation must
carry version evidence before Prime enables it. Codex V1 honors
`agents.max_depth`; Codex V2 ignores `agents.max_depth` and `ThreadSpawn` tracks
the effective nested depth. Cursor SDK supports configured subagents and nested
capabilities through the exact root → child → grandchild hierarchy; a
grandchild cannot spawn a great-grandchild. A parent worker must not assume it
can create another worker, inherit a parent's permissions, or share a mutable
path safely.
Patton Prime defaults nested worker spawn to `unavailable` and serial fallback,
even when an adapter marks it `native`; Prime enables it only after recording
the proven version, host tool, permitted depth, and policy. If the effective
Codex V2 cap is unproven, serial fallback is required. Cursor requests beyond
its grandchild limit use serial fallback. A nested mission still needs its own
identity, boundary, limits, report, and independent verification.

## Host notes

- [Claude Code adapter](../adapters/claude-code.md)
- [Codex adapter](../adapters/codex.md)
- [Cursor adapter](../adapters/cursor.md)
