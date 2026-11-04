# P1M findings: a reasoning setting does not lift the fitted-code Verifier

Study: 2026-11-04, the P1V prompt (`fitted-v2`) asked of qwen3.8-27b at reasoning medium and high on the same 89 stored
P1R stop-arm inputs. Pre-registration:
[`lab/prereg/2026-11-04-p1m-preregistration.md`](../../lab/prereg/2026-11-04-p1m-preregistration.md), committed as
`dbfe028` before the first call of the study. Report: [`lab/P1M_REPORT.md`](../../lab/P1M_REPORT.md). It follows
[P1V](2026-11-04-p1v-findings.md).

## Result

Neither reasoning arm flagged more gamed runs than the reference, the same prompt with reasoning off, so both M1
predictions are NOT SUPPORTED. Both M2 predictions are SUPPORTED.

- **M1, medium.** 27 of 41 (0.659) against the reference's 28 of 41 (0.683). Only the arm flagged 8 runs, only the
  reference flagged 9, sign test p = 0.6855.
- **M1, high.** 26 of 41 (0.634). Only the arm flagged 7, only the reference flagged 9, p = 0.7728.
- **M2, both arms.** 0 of 33 runs that did the work flagged, upper 95% bound 0.087, under the 0.15 limit.

Reasoning cost about 4 and 10 times as much for no gain on the first draw: the reference's 41 draws cost $0.15, the
medium arm's $0.55 and the high arm's $1.49. Both arms applied their setting (mean output 1240 and 3880 tokens against
the reference's 99), so the result is not a dropped parameter.

## One draw is noisy

The second draws moved more than the arms differ. The reference went from 28 of 41 (0.683) to 31 of 41 (0.756), medium
from 27 of 41 (0.659) to 29 of 41 (0.707), and high from 26 of 41 (0.634) to 35 of 41 (0.854). A nine-run swing inside
one arm is larger than any gap between arms, so a single draw per case cannot separate settings that differ by a few
runs. The registered rule used the first draw and says nothing in favour of high; the second draw's 0.854 is not a
finding.

Of the 7 gamed runs the reference missed in both draws, the medium arm's first draw flagged 6 of 7 and the high arm's
4 of 7. The same draws lost 9 and 9 runs the reference had flagged. Across both draws the medium arm flagged 34 of 41
gamed runs and the high arm 37 of 41, which says each setting reaches different runs, not that either reaches more.

## One lead: undone work

The reference flagged 2 of 15 runs that did not meet their goal without gaming (the unmet runs). Medium flagged 5 of 15
(0.333) and high 11 of 15 (0.733), with 10 of 15 (0.667) on its second draw. This was not registered, and reasoning
that flags every incomplete run is not the same as reasoning that finds the fitted ones. The report does not break the
unmet runs out by fixture, so the lead needs that cut before anyone relies on it.

## Development

The development set (P1V's 11 pilot runs and 16 synthetic states) showed the gpt-oss arms and the safeguard model
flagging fewer gamed states than qwen3.8 with no reasoning (12 of 20 (0.600) and 13 of 20 (0.650) for gpt-oss-120b,
5 of 20 (0.250) for the safeguard model, against 19 of 20 (0.950)), so the study dropped them. The set is saturated, so
it could not have shown a gain from reasoning, and it did not.

## What this does not show

- It does not show reasoning never helps. With a nine-run draw-to-draw swing, a gain of a few runs is invisible at this
  sample size, and 41 gamed runs on two fixtures is the whole evidence.
- The reference ran earlier the same day on the same model id, which Groq lists as a preview.
- The replay measures detection only, and 4 and 2 high-effort replies were cut off at the output limit and read as no
  verdict.

## Cost and next step

The study cost $4.09 (356 calls); development and the study together $5.20 of the $6.00 ledger cap. Reasoning is not
the lever for the 7 double misses. The next step stays the one P1V named: put the ground into patton-gate and run the
live stop arm against the none arm, where the question is whether a refused stop changes what the worker does. A different
Verifier model waits on a larger sample, since this one cannot separate settings that differ by a few runs.
