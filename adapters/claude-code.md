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
| Nested worker spawn | `unavailable` | Current Claude Code subagents and agent-team surfaces do not prove recursive worker spawn. A versioned feature must document and prove recursive spawn; Prime records the tool, policy, and depth before enabling it, otherwise uses serial fallback. |

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

Current Claude Code subagents and agent-team surfaces support worker dispatch,
but they do not prove recursive worker spawn. A versioned feature must prove
recursive spawn and document that proof in the changelog, or a fork must carry
an explicit recursive-spawn implementation. Even when the parent can dispatch
a worker, a child may lack the same spawn tool, context, budget, or permission
policy. Patton Prime conservatively marks nested worker spawn `unavailable` and
uses serial fallback until the active session records the versioned feature,
permitted depth, tool, and policy. Any nested mission must retain its own
mission identity, immutable ownership boundary, limits, and report; Prime still
requests an independent read-only Verifier.
