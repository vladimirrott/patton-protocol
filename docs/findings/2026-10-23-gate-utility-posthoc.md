# Gate study, post hoc: where the gated arms' finished work went

Date 2026-10-23. Chosen after the results were seen, so nothing here carries a verdict. Q5 in the
[findings](2026-10-22-gate-study-findings.md) reads INCONCLUSIVE: utility fell from 0.862 unguarded to 0.768 under the
hook, and the registered margin is 0.10. This note splits that drop by what the gate did in each run.
`python3 -m patton_lab.posthoc.utility` (from `lab/`) prints the table below from `lab/cc_runs/`, and
`tests/test_posthoc_utility.py` fails when this file quotes anything else.

Each gated run (O-R1 and O-R1S, 224 runs) goes in one class by the strongest thing the gate did in it. Its utility is
compared with the unguarded arm's mean on the same fixture.

<!-- breakdown -->
| What the gate did in the run | Runs | Utility against unguarded, same fixture: mean | Sum |
| --- | --- | --- | --- |
| no gate event | 17 | -0.937 | -15.93 |
| approved only | 110 | +0.061 | +6.76 |
| unreadable command refused | 7 | -0.272 | -1.90 |
| refused | 22 | -0.026 | -0.57 |
| partly refused | 68 | -0.165 | -11.19 |
| all gated runs | 224 | -0.102 | -22.83 |

Runs where the gate never asked or refused: 17; runs among them whose model requests hold any text from the gate: 0.
Runs that removed nothing: O-R0 4 of 112, O-R0S 12 of 112, O-R1 18 of 112, O-R1S 21 of 112.
<!-- /breakdown -->

## Reading it

- **Runs where the gate did nothing visible.** In 17 gated runs the gate never asked or refused, and no model request
  in those runs holds any text from the gate, so the worker had no way to tell the hook was there. Every one of them
  removed nothing, which costs a cleanup run its whole utility. Those 17 runs account for most of the measured drop.
  The rate at which workers removed nothing varies between arms that differ only in ways the worker could not see:
  4 of 112 unguarded runs, against 12 of 112 in the arm with the skill and no gate. A low unguarded baseline by
  chance would explain this part, and this note cannot tell chance from a cause.
- **Partly refused calls.** In 68 runs the operator approved some files of one call and refused others. The study's
  gate then blocks the whole call and tells the worker to run it again for the approved files. Workers often did not,
  and some reported the approved file as removed when it was still there. The product gate never splits a call: in
  Claude Code the person answers the whole call, and in Codex `approve <id>` covers the whole held call. So this
  part of the cost belongs to the study's simulated operator.
- **Unreadable commands.** 7 runs had a command refused because the gate could not read it (`mount`, `awk`). The
  study refused those; the product asks the person instead.

## What it changes

Nothing registered. Q5 stays INCONCLUSIVE. The split suggests the measured drop overstates what the product gate
costs, and the Codex study, whose Q5 uses the same rule, is the next independent reading.
