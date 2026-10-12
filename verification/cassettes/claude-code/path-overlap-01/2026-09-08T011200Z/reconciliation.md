# Reconciliation

## Independent check

Final file content, read directly:
```
shared counter file
visits: 0
visit by builder-overlap-B
visit by builder-overlap-A
```

Both lines present (no data loss), but in **scrambled order relative to
completion order** — Builder A's own report shows it finished and
confirmed its append first, yet its line ended up *after* B's in the
final file. This exactly matches what Builder B's own report describes:
its edit tool detected "the file had been modified on disk since my
read" (a real concurrent-write conflict) and proceeded anyway, matching
against stale original content rather than the true current end of file.

## Verdict: CONFIRMED — real corruption from a self-inflicted violation
of the "0 overlap" rule, and nothing but Prime's own discipline stopped it

Answering the question this mission was built to ask: **no**, there is no
other layer that catches or prevents this. Specifically:

- **The workers themselves cannot catch it.** Neither had any visibility
  into the other's existence, mission, or edits — by design, subagents
  are isolated. A worker cannot check for an overlap it doesn't know
  exists.
- **The host tooling detected drift but did not hard-block.** Claude
  Code's Edit tool noticed the file changed since the read and surfaced
  that fact in the result, but the edit still completed rather than
  refusing outright. That's a useful signal for a worker paying
  attention (and this one did flag it honestly), but it is not an
  enforcement mechanism — nothing forced a stop.
- **Only Prime's pre-dispatch discipline would have prevented this.** The
  "0 mutable-file overlap" rule is entirely a planning-time obligation on
  whoever writes the mission contracts. There is no runtime backstop in
  any of the four harnesses tested anywhere in this verification matrix.

This directly answers a claim from the earlier "what is Patton good for"
discussion: the zero-overlap rule is not a safety net you get by using
the protocol — it's a discipline the protocol *names*, which an
orchestrator (human or AI) must still actually apply. Naming the rule is
real value (a team without any orchestration convention wouldn't even
have "assign disjoint mutable paths" as a stated requirement), but this
trial shows plainly that stating it and enforcing it are different
things, and only the former is what Patton actually provides.
