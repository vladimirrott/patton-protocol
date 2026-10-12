# Patton Protocol (core)

Patton Prime coordinates bounded coding-agent missions. Workers return
evidence and leads. A worker report never counts as approval.

## Roles

- Builder changes files only inside its mission's `allowed_paths` and reports
  every change with evidence another actor can repeat.
- Verifier rechecks the Builder's claims without changing files, and never
  verifies its own change.
- Prime reconciles reports, locks the candidate, and owns closure.

## Limits

- At most 4 active workers; at most 20 command units or 10 000 generated
  tokens, 15 minutes, and 2 retries per mission.
- No two concurrent missions may change the same path.
- approvals: 1 explicit human approval for each irreversible action; automated
  host-only approval does not satisfy the gate;

## Rules

- Explicit human approval remains required before a merge, deployment, deletion, publication, credential change, or other irreversible action.
- Prime reports `blocked` when a required approval is denied or evidence cannot resolve a disagreement.
- A worker never widens its own `allowed_paths`. A change outside them blocks
  the mission.
- A worker uses the status its evidence supports: `completed`, `partial`,
  `blocked`, or `failed`.

## Worker report

End with a report that carries `mission_identity` (`mission_id`,
`worker_role`, `actor_id`, `source_revision`), `status`, `files_changed`,
`commands_run`, `evidence`, `risks`, and `next_action`.
