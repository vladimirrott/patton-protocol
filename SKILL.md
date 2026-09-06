---
name: patton-protocol
description: Coordinate bounded coding-agent missions through a portable scope, plan, dispatch, observe, reconcile, verify, and report lifecycle.
---

# Patton Protocol

Patton Protocol coordinates coding-agent work through a bounded, observable
lifecycle. It targets any AI tool that implements the canonical Agent Skills
`SKILL.md` format. The protocol describes capabilities and responsibilities;
the host chooses the mechanism that provides them.

## Lifecycle

Run each mission through these stages:

1. **Scope**: define the objective, inputs, ownership boundary, and stopping
   condition.
2. **Plan**: choose the smallest set of bounded missions and state the evidence
   each mission must return.
3. **Dispatch**: assign missions to available workers, or keep them in the main
   loop when the host cannot spawn workers.
4. **Observe**: collect progress, failures, timeouts, and structured worker
   reports.
5. **Reconcile**: compare reports, preserve disagreements, and integrate only
   changes that remain within their assigned boundaries.
6. **Verify**: independently check the candidate and its evidence against the
   mission objective.
7. **Report**: summarize status, changed files, commands, evidence, risks, and
   the next action.

The lifecycle remains valid when dispatch runs serially. Host-specific adapters
may map these capabilities to native surfaces, but they are optional.
