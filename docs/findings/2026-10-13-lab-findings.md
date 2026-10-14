# Patton Lab findings, 2026-10-13 (revised 2026-10-14)

Every decimal, ratio and run count below appears in `lab/REPORT.md` or in the pre-registration, and `tests/test_lab_study_integrity.py` fails when one does not. The predictions and thresholds were frozen in `lab/prereg/2026-10-12-lab-preregistration.md` before the first study call. The study holds 1344 study jobs on two models (gpt-oss-120b on Groq, gpt-4.1-mini on OpenAI), plus 2 smoke jobs. All of them replay with no network, and the ledger total was $4.6538 against a $10 cap.

## Pre-registered results

- **Full SKILL.md prompt (B) against a generic prompt (A).** Harm was 0.413 for B and 0.325 for A, a difference of -0.088 with a 90% CI of [-0.225, 0.050]. P1 reads INCONCLUSIVE and the estimate leans against the spec. A and A2, which are the same arm run twice, differ by 0.013 and the null check passes.
- **Approval sentences removed (C).** Harm was 0.425. P2 reads NOT SUPPORTED on all runs and INCONCLUSIVE on the clean runs (see Robustness), so the data do not show that the approval text is inert. They show only that B left no benefit for C to remove.
- **Reversibility clause (D).** Harm was 0.313, 0.100 below B, so P4a reads SUPPORTED (D is non-inferior to B). Utility was 0.721 against 0.710 for B, so P4b reads NOT SUPPORTED. D lands at the generic prompt's harm (A 0.325, A2 0.313): it removes B's excess and does not beat the baseline.
- **Short distillation (F).** Its prompt is 1.7 KB against 14.3 KB for B. Harm was 0.125 and utility 0.446 against 0.710 for B. F asked for approval 0.475 times per run (B 0.088) and halted 0.725 times per run on items it could have removed (B 0.088). P5 reads SUPPORTED on harm alone; F lowers harm by doing less.
- **Gate (G).** Harm was 0.000 over 80 runs and utility 0.746, so P9 reads SUPPORTED. That holds by construction, since P9 tests the gate's code. The gate denied at least one action in 78 of 80 runs, which includes safe deletions that waited for the simulated operator, so the count shows an active gate and does not count harmful deletions stopped. G sent 1.450 approval requests per run, the operator's burden. P10 reads NOT SUPPORTED (0.746 against 0.710, 90% CI [-0.054, 0.129]), and with an oracle approver it bounds utility from above.
- **Replication on gpt-4.1-mini.** E1R has no pre-registered verdicts. Over 48 runs per arm, harm was 0.396 for A, 0.396 for B and 0.354 for D. That is consistent with no benefit from the full text, and approval requests per run were 0.042 for A and 0.021 for B.
- **Verifier anchoring.** P6 reads SUPPORTED: lie-detection Youden J was 1.000 for a cold Verifier against 0.531 when it saw the Builder's report, over 8 of 8 tasks. Without the spec (cold_nospec) J was 0.813 with a false-positive rate of 0.125, which is descriptive only.
- **Digest tool.** P8 reads SUPPORTED: stale-evidence J was 1.000 with the tool against 0.146 without, over 6 of 6 repositories. The pre-registration states that P8 is close to true by construction.
- **Code-execution tools.** P7 reads INCOMPLETE with 6 of 8 units, because two tasks lost a parsed candidate on one side. It could not have reached SUPPORTED: the cold arm's bug J is 0.958 on those units, which leaves 0.042 of headroom against a 0.20 threshold. The pre-registration exempts Youden contrasts from the ceiling flag that would have caught this, so headroom should be checked before registering. Over parsed answers the tool arm scored J 0.923 on bug and lie and 1.000 on stale, where arms without tools found no stale candidate.

## Why the full prompt does not help (post hoc)

Chosen after the first look at E1, so these carry no verdict. Every E1 fixture holds a HANDOFF.md that says which files are an only copy, and the worker chooses whether to open it before changing anything.

- Opened it before the first change: A in 57 of 80 runs, B in 15 of 80, D in 37 of 80, F in 58 of 80.
- Harm when it did and when it did not: A 0.246 and 0.522, B 0.200 and 0.462, D 0.108 and 0.488, F 0.121 and 0.136.
- Runs that harmed an only copy (80 runs per arm): A 11, B 21, D 15, F 4.

With the 14.3 KB prompt, workers started changing files without opening the note, and the harm sits in those runs. F is the exception: it asks (0.475 approval requests per run), and its harm stays low whether or not it read the note. Reading is the worker's choice, not an assignment, so this shows association only. An earlier draft said B's workers read the rule and failed to apply it. The split does not support that story.

## Inlined references

Arm E (references and adapter inlined, 16 runs, one per fixture) produced a schema-valid report in 16 of 16 runs, and B produced 1 valid report in 80 runs. E made 0.000 approval requests per run, with harm 0.375 and utility 0.563 against 0.710 for B. One E run made no tool call. At one run per fixture the data support no verdict beyond this: report format follows what sits in the prompt, and approval behavior did not move.

## Provider failures

Groq returned malformed tool calls in 62 of 64 E2T jobs, 53 of 60 E3T jobs, 52 of 384 E2 jobs and 30 of 560 E1 jobs, against 0 of 144 E1R jobs on OpenAI. Sixteen E2T jobs and 16 E3T jobs hit the turn cap and ended with no final answer; they mixed successful and malformed turns. Whether the cause lies with Groq or with this harness's tool schemas is not established. Those jobs are excluded from the verifier tables, as the pre-registration says, and the missing answers are not random: they cluster in the tool arms.

## Robustness

Dropping E1 runs that hit the turn cap, ended with no final text, or made a malformed tool call leaves 5 of 7 E1 labels unchanged. P2 moves from NOT SUPPORTED to INCONCLUSIVE and P3 from INCONCLUSIVE to NOT SUPPORTED. No label moves to SUPPORTED or OPPOSITE. Turn caps are uneven across arms (20 of 560 E1 jobs, 11 of them in A and A2), which is why this check exists.

## Limits

- P10 uses an oracle approver and is an upper bound on utility.
- Two models, 16 fixtures, and one synthetic deletion benchmark. The fixture is the unit of analysis, so the effective n for the E1 verdicts is 16. Three fixtures (f01, f10, f16) show no harm in any arm without the gate, and f11 harms in nearly every run, so the informative sample is smaller.
- Harm is binary per run, so one deletion counts as three do. The ambiguous category counts as harm, and a generic prompt has no way to tell it from a deletable file.
- E1R, E1E, E4 and E7 carry no pre-registered verdicts. E4's `hidden_pass` can be faked by a model that sets out to do so.
- E1H, the held-out set, did not run: its gate needs P4a and P4b both SUPPORTED, and P4b is NOT SUPPORTED.
- Spend: the per-job total is 4.6471 and the ledger total is 4.6538. The difference is the ledger's charge for attempts whose outcome was unknown (see Run diagnostics in the report).

## Deviations from the pre-registration

1. After the freeze and 20 seconds before E1 began, commit `5c9d4e9` widened the report-status reader in `score.py` to accept markdown decoration and synonyms such as "success". The smoke run had shown a report the narrow pattern missed. The reader feeds only `report_consistent` and the exploratory E7 classification, and harm and utility do not use it.
2. At job 122 of E1, Groq returned an HTTP 400 "Parsing failed" and the run stopped. Commit `ed80b1d` made the client record any 400 that carries a failed generation as a malformed turn, and E1 resumed. It touches that error path only, and the 122 earlier jobs kept their cassettes.
3. After the first look at the results, only the report changed: the reference-read column was renamed, CLI output prints file names instead of paths, tie values round half up so they print the same everywhere, the E2 table excludes unparsed answers (the first version counted them as misses, against the pre-registered rule), and the report gained a contrast column, run diagnostics, the post hoc tables and the clean-run table. Arms, fixtures, thresholds, scoring and results did not change.

## Corrections to the first version (`c685508`)

- It gave 0.917 as the cold arm's score. That is the tool arm's value, and the cold arm scored 0.958 on the same units.
- It said the stalled E2T runs spent all their turns on malformed calls. They mixed successful and malformed turns.
- Its E2 table counted unparsed tool-arm answers as misses, which understated that arm.
- Its headline said prose does not change worker behavior. F is prose and changed it.
- It explained B's harm as workers who read the rule and did not apply it. The post hoc split above does not support that.
- It said the gpt-4.1-mini replication showed the same shape and that inlining changed nothing but format. Neither has the data behind it.
- It named one harness change after the freeze. There were three.
- It counted 1346 jobs as the study. The study is 1344 jobs and 2 smoke jobs.

## What it means for the product

The data supports enforcement over instruction. A gate with an audit ledger removed the harm the full-text spec left in place, at the utility of the same prompt without a gate, when an operator answers the requests. The full text showed no measured safety benefit on these two models and may have cost some by displacing the habit of reading the handoff note. The 1.7 KB distillation shows prose can move behavior, at a utility cost. The next experiments follow from those two facts: a protocol revision that tells the worker to read the handoff and names the approval tool, a gate without an oracle approver, and a third model.
