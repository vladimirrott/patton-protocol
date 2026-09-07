# Codex Adapter

This adapter maps Patton Protocol capabilities to Codex custom agents and
skills. The canonical skill and its contracts work without this adapter.

## Capability matrix

| Capability | Status | Host mapping and boundary |
| --- | --- | --- |
| Loading | `native` | Load the package through skill discovery or an explicit skill invocation. |
| Worker definition | `native` | Define a custom agent or subagent for one bounded mission, and pass its mission contract unchanged. Skills provide the worker instructions. |
| Parallel dispatch | `native` | Use multi-agent support where the active Codex host enables it, with one immutable mutable-path boundary per Builder. |
| Serial fallback | `prompt-mediated` | Run each mission in the main loop when the host cannot spawn or isolate workers. Preserve the independent Verifier and candidate identity checks. |
| Approval boundary | `prompt-mediated` | Request one explicit human approval for each irreversible action. Sandbox, confirmation, or automation state does not grant protocol approval. |
| Nested worker spawn | `native` | Codex V1 honors `agents.max_depth`; Codex V2 ignores `agents.max_depth` and relies on the effective depth tracked by `ThreadSpawn`. Prime records the V2 cap before enabling nested work; an unproven cap requires serial fallback. |

## Mapping guidance

Codex custom agents and skills carry the worker role, objective, allowed paths,
stopping condition, limits, and evidence requirements. Multi-agent support can
dispatch independent missions in parallel when enabled. Prime owns dispatch,
reconciliation, candidate locking, and final reporting, even when the host
starts workers natively.

When no worker primitive is available, Codex runs the same mission envelopes in
serial fallback. Prime assigns distinct Builder and Verifier actor identities,
starts Observe around bounded verification, and preserves partial or failed
reports. A same-actor Builder and Verifier result remains `blocked` and
`unverified`.

Codex approval prompts and sandbox permissions protect host actions, but they
do not replace the protocol approval boundary. Before a merge, deployment,
deletion, publication, credential change, or other irreversible action, Prime
records the human approver, exact scope, `approved` decision, and verified
evidence. Workers and Verifiers return leads; they cannot approve their own
work.

Canonical approval boundary: host-only approval does not satisfy or replace
explicit human approval and cannot authorize an irreversible action.

## Nested-spawn limitation

Codex V1 honors `agents.max_depth` as its configured nested-depth limit. That
setting is V1-only and Codex V2 ignores `agents.max_depth`; V2's
`ThreadSpawn` protocol records the effective nested depth and its cap. Custom
agents can have different tools, permissions, and session context from the
parent, and a worker's ability to run a skill does not prove its ability to
create another worker. Codex hosts may expose multi-agent dispatch only at the
top level. Patton Prime conservatively defaults nested worker spawn to
`unavailable` and serial fallback until the active session records the
`ThreadSpawn` tool, effective V2 cap, and policy. If that effective V2 cap is
unproven, serial fallback is required. Nested work still needs a separate
mission identity, boundary, budget, report, and independent verification.
