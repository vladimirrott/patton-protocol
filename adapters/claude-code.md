# Claude Code Adapter

This adapter maps Patton Protocol capabilities to Claude Code surfaces. The
portable contracts remain authoritative, and the host may require local
configuration before a surface is available.

## Capability matrix

| Capability | Status | Host mapping and boundary |
| --- | --- | --- |
| Loading | `native` | Load the package through skill discovery or an explicit skill invocation. |
| Worker definition | `native` | Define a bounded mission with a custom subagent or an agent team member. Pass the complete mission contract and immutable allowed paths. |
| Parallel dispatch | `native` | Dispatch independent missions through subagents or agent teams when the host has enabled that surface and ownership paths are disjoint. |
| Serial fallback | `prompt-mediated` | Run missions in order in the main loop when spawning or isolation is unavailable. Keep the distinct Verifier identity and all evidence checks. |
| Approval boundary | `prompt-mediated` | Ask a human for explicit approval before an irreversible action. Tool permissions and host policy do not count as that decision. |
| Nested worker spawn | `native` | The Claude Code 2.1.263 changelog documents recursive spawn for subagents and agent teams, with default depth 3 and a host environment override. Prime records the proven version, tool, policy, and depth; unknown or version-unproven surfaces remain `unavailable` with serial fallback. |

## Mapping guidance

Custom subagents and agent teams can provide the worker definition and parallel
dispatch surfaces. Prime still limits active workers, checks mutable-path
overlap, and records each worker's start, commands, changed paths, budget, and
report. Agent-team availability, permissions, and nested spawning can differ
between sessions, so Prime probes the active capability matrix rather than
assuming that a configured team is available.

Use skill discovery for loading when available. An explicit invocation may
load the same package when discovery is disabled, but it does not alter the
portable lifecycle. The adapter does not require a particular command syntax.

Claude Code's permission or confirmation UI is not the protocol's approval
boundary. Prime must obtain a named human approver and record the exact scope,
decision, and candidate evidence. A worker cannot approve its own change, and
a denied or missing approval leaves the mission `blocked`.

## Nested-spawn limitation

The Claude Code 2.1.263 changelog documents recursive spawn for subagents and
agent teams. The default maximum depth is 3, and a host environment override can
change that depth. A worker may still lack the same spawn tool, context, budget,
or permission policy as its parent. Patton Prime marks nested worker spawn
`native` only for the proven version and recorded depth, tool, and policy.
Patton Prime conservatively treats an unknown version or version-unproven
surface as `unavailable` and uses serial fallback. A fork must carry an explicit recursive-spawn implementation and
version evidence before Prime treats it as proven. Any nested mission must
retain its own mission identity, immutable ownership boundary, limits, and
report; Prime still requests an independent read-only Verifier.
