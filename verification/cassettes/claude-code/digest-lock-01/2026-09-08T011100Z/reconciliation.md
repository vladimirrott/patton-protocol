# Reconciliation

## Verdict: PASS — the mechanism, actually implemented and exercised for
the first time in this verification matrix, does what the spec claims

**Scope-violation detection (step 3):** a fabricated worker report
claiming only `fileA.txt` changed did not matter. Computing
`candidate_tracked_paths` from real git state exposed `fileB.txt` as
undisclosed and out-of-boundary regardless of the claim. This is the
first real (if partly simulated) evidence that "Prime computes the
candidate identity independent of the Builder's report" is a checkable
property, not just prose — last session's claim that this was "not
proven" is now, for the scope-violation half, addressed.

**Staleness detection (step 4):** a post-lock mutation was correctly
detected as invalidating the locked candidate. More importantly, this
step surfaced something not previously stated anywhere in this matrix:
**`candidate_revision` alone would have missed it.** Git `HEAD` doesn't
move on an uncommitted working-tree edit, so a Prime implementation that
checked only the revision (not the digest) would have accepted stale
evidence silently. `content_digest` is load-bearing specifically for the
uncommitted-mutation case, which is the common case for a Builder's
in-progress work (most missions in this whole verification matrix never
committed anything). This is a genuine, useful clarification of the spec
that this trial produced as a side effect, not something it set out to
find.

## What this does NOT prove

- This tests the mechanism's logical soundness given a real git repo. It
  does not test whether any real AI host, acting as "Prime," would
  actually think to run this check unprompted — every prior trial's
  "Prime" role was played by me, the dispatching session, not by an
  autonomous host applying the spec on its own initiative. Whether an AI
  orchestrator reliably remembers to do this without being told is a
  separate, still-open question.
- The digest algorithm itself has failure modes this trial didn't probe:
  non-UTF-8 filenames, actual symlinks/gitlinks (the spec's rejection
  rules for both), and the untracked-path classification rules (`candidate_
  untracked_paths` vs `non_candidate_untracked_paths`) were not exercised
  — this trial used only tracked files.
- No-git-repo host: still untested. This trial deliberately used a git
  repo to test the algorithm as specified; the `"candidate:" + content_
  digest` fallback path for a host with no revision remains unexercised
  by an actual mutation (every prior no-git trial ended `blocked` with no
  mutation, so fell into the null/null pre-candidate shape instead).
