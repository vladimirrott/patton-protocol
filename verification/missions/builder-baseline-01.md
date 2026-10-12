# Mission: builder-baseline-01

Fixed after first use (2026-09-07, Claude Code). Do not edit; a changed
scenario is a new mission id.

## Purpose

The simplest possible test that a real host can carry a mission through
Dispatch → Observe → Reconcile → Verify at all: one Builder writes a small,
independently checkable piece of code, one Verifier independently re-derives
correctness with zero access to the Builder's report.

## Contract given to the Builder worker

```
actor_id: builder-01
objective: Write is_prime(n) in <scratch>/is_prime.py, and a test suite in
  <scratch>/test_is_prime.py, covering negatives, 0, 1, 2, 3, small primes,
  small composites, and at least one larger prime and larger composite.
allowed_paths: exactly <scratch>/is_prime.py and <scratch>/test_is_prime.py
budget: small (single-digit tool calls)
timeout: a few minutes
retries: 0
report shape: status, summary, full file contents, confirmation the test
  suite was actually run and passed (not merely written)
```

`<scratch>` is a fresh, empty, non-git scratch directory local to the
dispatching host — this mission does not exercise git-repo-backed
candidate-manifest handling.

## Contract given to the Verifier worker

```
actor_id: verifier-01
objective: Independently verify is_prime.py's correctness and that
  test_is_prime.py is a genuine, non-vacuous suite — with NO access to the
  Builder's report, starting cold.
allowed_paths: read-only, the same two files, plus execution rights
must: run the existing suite and report real command output; try edge
  cases the Verifier chooses itself, not ones copied from the Builder;
  attempt at least one mutation of the implementation to confirm the suite
  actually fails when the code is wrong (proves the suite isn't a rubber
  stamp)
report shape: status, summary, evidence (verbatim commands + output),
  independent_test_cases, verdict (CORRECT/INCORRECT/UNABLE_TO_VERIFY),
  anomalies
```

## Pass condition

Verifier reaches CORRECT independently, its own edge cases and mutation
test all behave as a correct implementation and a real test suite would,
and reconciliation (a third read, by whoever dispatched both workers) finds
no discrepancy between Builder's claim and Verifier's independent finding.
