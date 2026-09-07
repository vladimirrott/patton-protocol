# Cursor Adapter

This adapter maps Patton Protocol capabilities to Cursor Agent Skills and
plugin-agent surfaces. Adapter availability depends on the active Cursor
workspace and installed configuration; the portable core has no such
dependency.

## Capability matrix

| Capability | Status | Host mapping and boundary |
| --- | --- | --- |
| Loading | `native` | Load the package through Agent Skills discovery or an explicit skill invocation. |
| Worker definition | `native` | Define a plugin agent or subagent for one mission, with the role, immutable allowed paths, limits, and evidence contract. |
| Parallel dispatch | `prompt-mediated` | Ask the active agent or plugin surface to dispatch independent missions when that support is enabled and path ownership is disjoint. |
| Serial fallback | `prompt-mediated` | Execute missions in order in the main loop when parallel agent support is unavailable. Retain the mandatory independent Verifier and approval gate. |
| Approval boundary | `prompt-mediated` | Obtain explicit human approval before an irreversible action. Workspace permissions or an automated agent decision do not satisfy the gate. |
| Nested worker spawn | `unavailable` | Plugin agents and subagents cannot assume recursive spawning or inherited permissions. Prime defaults to serial execution unless the active session proves otherwise. |

## Mapping guidance

Cursor skills provide the loading and worker-instruction surface. Plugin agents
or subagents may provide worker definitions, and some workspaces may expose
parallel dispatch. Because plugin installation, workspace policy, and agent
surfaces vary, Prime records `prompt-mediated` when a human or host prompt
coordinates dispatch and uses serial fallback when the surface cannot be
verified.

Serial fallback preserves the mission and report envelopes, immutable
ownership, timeout and budget limits, candidate identity, evidence rechecking,
and distinct Builder and Verifier actor identities. If Cursor cannot provide a
distinct Verifier actor, a human performs verification. A same-actor result
stays `blocked` and `unverified`.

Cursor's workspace confirmation or permission controls do not constitute the
protocol approval boundary. Prime records a human approver, exact scope,
`approved` decision, and verified evidence before a merge, deployment,
deletion, publication, credential change, or other irreversible action. A
worker, Verifier, or plugin agent cannot approve its own work.

## Nested-spawn limitation

Plugin agents and subagents may run with separate context, tools, and
permissions. A parent agent's dispatch surface does not prove that a child can
spawn another worker. Treat nested worker spawn as `unavailable` unless the
active workspace explicitly proves it and Prime records the separate mission
boundary. Nested missions retain independent identity, limits, reports, and
verification; absent that proof, Prime runs serially.

