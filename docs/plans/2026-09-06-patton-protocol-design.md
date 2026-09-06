# Patton Protocol Design

Date: 2026-09-06
Status: approved

## Purpose

Patton Protocol is a general, harness-agnostic workflow skill for coordinating
coding agents. It targets the canonical Agent Skills `SKILL.md` format, so the
compatibility boundary is any AI tool that implements that standard. Claude
Code, Codex, and Cursor are initial documented adapters, not the only supported
hosts.

Patton helps an agent decide when delegation will improve a task, decompose the
work into bounded missions, collect structured reports, reconcile conflicting
results, and verify the final candidate. It keeps small tasks in one agent.

## Goals

- Provide one portable orchestration protocol in a canonical `SKILL.md`.
- Make delegation explicit, bounded, observable, and resumable.
- Separate planning, implementation, review, and verification responsibilities.
- Give every worker the same mission and evidence contract.
- Document host-specific mappings without coupling the core to a vendor API.
- Support serial fallback when a host cannot spawn workers.
- Make safety and approval boundaries part of the protocol, not implied by a
  model prompt.

## Non-goals

- A runtime dispatcher, daemon, or MCP server in the first release.
- A claim that every host can spawn nested agents.
- Vendor-specific permission guarantees in the portable core.
- Automatic publishing, merging, deployment, or other irreversible actions.
- A military simulation or roleplay layer that distracts from engineering work.

## Architecture

The repository will contain one canonical skill and supporting references:

```text
patton-protocol/
├── SKILL.md
├── references/
│   ├── mission-contract.md
│   ├── worker-report.md
│   ├── harness-adapters.md
│   └── safety-and-budgets.md
├── adapters/
│   ├── claude-code.md
│   ├── codex.md
│   └── cursor.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── validate_protocol.py
├── tests/
│   └── test_validate_protocol.py
└── docs/
    └── plans/
```

`SKILL.md` will contain the trigger description, portable lifecycle, and links
to resources. References will carry detailed contracts and examples. Adapter
files will describe equivalent host actions and known limits. Optional metadata
will improve discovery on hosts that support it while remaining nonessential to
the canonical skill.

## Protocol

The lifecycle is:

```text
scope → plan → dispatch → observe → reconcile → verify → report
```

Patton Prime owns the lifecycle. It may delegate only bounded missions with a
defined objective, inputs, allowed paths, stopping condition, and evidence
requirements. A worker returns a structured report containing mission identity, status,
files changed, commands run, evidence, risks, and next action. Patton treats a
worker report as a lead until an independent verification step confirms it.

The protocol defines four default worker roles:

- Scout: repository and dependency investigation.
- Builder: implementation in an assigned boundary.
- Verifier: exact-candidate tests and behavioral proof.
- Quartermaster: integration, state, budget, and handoff bookkeeping.

Patton may combine or omit roles when the task does not need them. It must not
fan out work when missions share mutable files, depend on unresolved decisions,
or cost more coordination than the task warrants.

## Harness portability

The core will name capabilities, not commands. An adapter maps capabilities to
the host's native surfaces:

| Capability | Claude Code | Codex | Cursor |
|---|---|---|---|
| Load the protocol | skill discovery or explicit invocation | skill discovery or explicit invocation | Agent Skills discovery or explicit invocation |
| Define a worker | custom subagent or agent team | custom agent or subagent | plugin agent or subagent |
| Parallel work | native subagents or agent teams | native multi-agent support where enabled | native agent/plugin support where enabled |
| No-spawn fallback | serial missions in the main loop | serial missions in the main loop | serial missions in the main loop |

The adapter documentation will mark behavior as `native`, `prompt-mediated`, or
`unavailable`. The skill will never promise a host action that the adapter
cannot prove.

## Safety and control

- Default to the smallest worker count that covers independent work.
- Set a mission budget, timeout, and maximum retry count before dispatch.
- Keep production writes in explicit worker boundaries.
- Require a human or host approval gate for irreversible actions.
- Never let a worker approve its own work or promote its own evidence.
- Preserve partial reports when a worker fails, times out, or loses its host.
- Surface unresolved disagreement instead of selecting a convenient majority.

## Naming and visual identity

The package name is `patton-protocol`. The coordinator is `Patton Prime`; role
names remain functional and optional. The logo will use an original abstract
five-point star with three branching arrows, in olive, cream, and signal red. It
will avoid copied military insignia, portraits, flags, and government marks.

The name is not treated as legally clear. Before publication, check package
registries, GitHub namespaces, and trademark conflicts, including the existing
Patton AI organization.

## Verification

The first implementation must validate:

1. Canonical frontmatter and required `SKILL.md` structure.
2. Every relative reference resolves inside the package.
3. Core text contains no vendor-only command as a required step.
4. Adapter files identify supported, mediated, and unavailable capabilities.
5. Worker reports satisfy the required schema.
6. Serial fallback completes the lifecycle without spawning.
7. Fixtures cover timeout, partial failure, conflicting reports, and denied
   irreversible actions.

The test harness will inspect protocol artifacts and simulate host capability
matrices. It will not call a model or require a vendor account.

## Distribution

The repository will publish the canonical skill as a standalone Agent Skills
package. Host-specific installation instructions will point to the same package
and will state each host's discovery path. A future dispatcher may consume the
mission and report schemas without changing the core skill contract.
