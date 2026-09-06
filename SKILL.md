---
name: patton-protocol
description: Coordinate bounded coding-agent missions when independent delegation improves the task.
---

# Patton Protocol

Patton Protocol is a portable Agent Skills package for coding-agent
coordination. It targets any AI tool that implements the canonical Agent Skills
`SKILL.md` format.

The mission and report contracts define the data exchanged across each
lifecycle stage: [mission contract](references/mission-contract.md) and
[worker report](references/worker-report.md). When a host cannot spawn workers,
Patton Prime runs bounded missions in serial order and keeps the same contracts.

## Lifecycle

The lifecycle stages are:

### Scope

Placeholder for the scope stage.

### Plan

Placeholder for the plan stage.

### Dispatch

Placeholder for the dispatch stage.

### Observe

Placeholder for the observe stage.

### Reconcile

Placeholder for the reconcile stage.

### Verify

Placeholder for the verify stage.

### Report

Placeholder for the report stage.

## Contracts

Workers use the [mission contract](references/mission-contract.md) for scope,
inputs, limits, and evidence requirements. They return a [worker report](references/worker-report.md)
with status, evidence, risks, and a next action.
