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
| Parallel dispatch | `native` | Dispatch independent missions through Cursor subagents or plugin agents when enabled and path ownership is disjoint; use serial fallback when the surface is unavailable. |
| Serial fallback | `prompt-mediated` | Execute missions in order in the main loop when parallel agent support is unavailable. Retain the mandatory independent Verifier and approval gate. |
| Approval boundary | `prompt-mediated` | Obtain explicit human approval before an irreversible action. Workspace permissions or an automated agent decision do not satisfy the gate. |
| Nested worker spawn | `native` | Cursor editor, CLI, plugin-agent, and SDK surfaces support root → child → grandchild. A grandchild cannot spawn a great-grandchild. Prime requires an explicit Patton policy cap no greater than the host limit and uses serial fallback beyond it. |

## Mapping guidance

Cursor skills provide the loading and worker-instruction surface. Plugin agents
or subagents provide worker definitions and native parallel dispatch when the
active workspace enables them. When the parallel surface is unavailable, Prime
uses serial fallback. If a workspace exposes only a prompt-coordinated
dispatch surface, Prime records `prompt-mediated` and checks the same ownership
boundary before dispatch.

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

Canonical approval boundary: host-only approval does not satisfy or replace
explicit human approval and cannot authorize an irreversible action.

## Nested-spawn limitation

Cursor editor, CLI, plugin-agent, and SDK surfaces support root → child →
grandchild. A grandchild cannot spawn a great-grandchild. The June 2026 Cursor
SDK changelog documents this configured nesting surface. An explicit Patton
policy cap covering depth, tools, and permissions is required, and cannot
exceed the host limit. Unknown or unconfigured policy stays `unavailable` with
serial fallback. A nested mission retains independent identity, limits,
reports, and verification.
