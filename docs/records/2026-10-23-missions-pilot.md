# Record: a pilot of the subagent limits

Date 2026-10-23. A pilot: no pre-registration, so nothing here is a verdict. Harness: `lab/patton_lab/missions/`;
records and cassettes: `lab/missions_pilot/`; ledger: `lab/missions_spend.jsonl`. `python3 -m
patton_lab.missions.report` (from `lab/`) prints every table below, and `tests/test_missions_pilot.py` fails when
this file quotes anything else.

## What ran

<!-- facts -->
- 16 runs, Claude Code 2.1.309, model gpt-oss-120b through Groq, 1336 model calls, all recorded.
- Spend: $2.1469 on the stored runs; the pilot's ledger holds $2.1469.
<!-- /facts -->

Both arms ran the patton-gate plugin with file protection off, so any refusal came from a subagent limit. M-OFF set
no limits; M-ON set the `patton` preset. Each of four fixtures asks the main agent to send two subagents at once,
each changing its own file and one shared file (a changelog, a JSON settings file, a README index, a test file),
then to check both changes and finish anything missing. Each arm ran each fixture twice, in a shuffled order. A $0
trace with a scripted model came first: without limits, a late shell write lost the first subagent's line; with the
preset, `exclusive_paths` refused it.

## Results

<!-- summary -->
| arm | runs | utility | lost updates | dispatched in one message | subagents ran at once | subagent commands per run | limit refusals | cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M-OFF | 8 | 0.875 | 0 | 0 of 8 | 7 of 8 | 34.0 | 0 | $1.4209 |
| M-ON | 8 | 1.000 | 0 | 0 of 8 | 8 of 8 | 14.5 | 1 | $0.7260 |
<!-- /summary -->

## Reading it

- **Lost updates did not happen.** No run in either arm ended with a shared file missing a line two subagents
  wrote, so the harm `exclusive_paths` targets did not occur here. Its one refusal, in M-ON on m01, cost the run
  nothing: both changes were in place at the end.
- **The model dispatched one subagent per response.** gpt-oss-120b never sent two dispatches in one message,
  though every mission asked for it. Claude Code runs subagents in the background, so they still overlapped: 7 of 8
  runs without limits and 8 of 8 with them.
- **Two runs without limits went runaway.** The main agent kept dispatching the same change under new wordings,
  19 and 10 times. No run with the preset did that, but no limit refused a dispatch in M-ON either, so the pilot
  cannot credit the limits, and with 8 runs per arm the gap may be chance. Those two runs account for most of the
  cost difference.
- **One run without limits scored 0.** On m04 the headless session ended while both subagents were still working,
  and nothing changed. It shows how `claude -p` ends a run, and it can happen in either arm.

## Post hoc: what the preset would have refused in those runs

Chosen after the results were seen. Each M-OFF run's dispatches are replayed in order through the preset's dispatch
rules; `tests/test_missions_pilot.py` checks that the replay refuses exactly what the plugin's own check refuses on
the same sequence.

<!-- replay -->
| M-OFF run | dispatches | refused for max_retries | refused for max_dispatches |
| --- | --- | --- | --- |
| M-OFF-m01-r0 | 2 | 0 | 0 |
| M-OFF-m01-r1 | 19 | 2 | 5 |
| M-OFF-m02-r0 | 3 | 0 | 0 |
| M-OFF-m02-r1 | 2 | 0 | 0 |
| M-OFF-m03-r0 | 3 | 0 | 0 |
| M-OFF-m03-r1 | 2 | 0 | 0 |
| M-OFF-m04-r0 | 10 | 3 | 0 |
| M-OFF-m04-r1 | 3 | 0 | 0 |
<!-- /replay -->

`max_retries` counts a mission as the same only when its description and type match, so rewordings pass it. This
pilot led to `missions.max_dispatches`, a cap on the subagents one session dispatches; the preset sets it to 4
workers times one dispatch plus two retries. In the 19-dispatch run it would have refused the dispatches past that
cap, so the runaway stops sooner, though not at the two the task needed.

## What it does not show

Whether the limits help. That needs a pre-registered study on a model that dispatches subagents the way people's
sessions do, with fixtures where lost updates happen often enough to measure. The trace also showed that a file
opens again when its owner stops, so a later subagent can still overwrite the earlier one's change.
