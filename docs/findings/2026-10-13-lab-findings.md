# Patton Lab findings, 2026-10-13

Every number below is printed in `lab/REPORT.md`, which `python3 -m patton_lab.run report` generates from `lab/results/`. The predictions and thresholds were frozen in `lab/prereg/2026-10-12-lab-preregistration.md` before the first study call. All 1346 recorded jobs replay byte-for-byte with no network (`python3 -m patton_lab.run replay`). Total spend was $4.65 of a $10 cap.

## What the data says

**Prose does not change worker behavior on deletion.** In E1, 16 deletion fixtures with 5 replicates per arm, the worker without the spec (A) harmed 0.325 of runs and the worker with the full SKILL.md in its prompt (B) harmed 0.413. The difference is -0.087 with a 90% CI of [-0.225, 0.050], so P1 reads INCONCLUSIVE and the point estimate leans the wrong way. Removing every approval sentence (C) left harm at 0.425, so P2 reads NOT SUPPORTED: there is no effect to remove. A and A2, the same condition run twice, differ by 0.013 and the null check passes.

The transcripts show why. A harmed B run read the rule, deleted a checkpoint without calling `request_approval`, and reported `completed`. Approval requests per run are 0.087 for B and 0.113 for A. The model has the rule and the tool and does not connect them.

**Inlining the references changes report format and nothing else.** Arm E (references and adapter inlined, 16 runs) produced a schema-valid report in 16 of 16 runs, against 1 of 80 for B. Its harm is 0.375 and its approval requests per run are 0.000. Format compliance follows what sits in the prompt. Approval behavior does not.

**A short distillation cuts harm by doing less.** The 1.5 KB distillation (F) passes P5 on harm at 0.125, and its utility falls to 0.446 against 0.710 for B. P5 tests harm only, so the SUPPORTED label overstates it. F also drew the most malformed tool calls (15).

**Enforcement works.** With the gate on (G), harm is 0.000 over 80 runs and utility is 0.746, so P9 reads SUPPORTED. The gate denied at least one action in 78 of 80 runs. P10, that gate plus approver beats prose on utility, reads NOT SUPPORTED (0.746 against 0.710, CI includes 0). Safety costs no measurable utility here. It does not buy any either.

**Replication.** gpt-4.1-mini (E1R, arms A, B and D, 48 runs each) shows the same shape: A and B both at 0.396 harm. A second model family points the same way, which is more than the first model alone, and still two models.

**Verifier detection.** Showing the Verifier the Builder's report lowers lie detection (P6 SUPPORTED, Youden J 1.000 cold against 0.531 anchored, 8 of 8 tasks). A digest tool lifts stale-evidence discrimination from 0.146 to 1.000 (P8 SUPPORTED, 6 of 6 repositories). P7, whether code execution raises bug detection, reads INCOMPLETE (6 of 8 units).

## Limits that bind the claims

- P9 tests the gate's code, so it holds by construction. P10 uses an oracle approver and is an upper bound. P8 is near-true by construction, since the tool computes the answer.
- P7 is incomplete for a provider reason. Groq returned `tool_use_failed` on 62 of 64 E2T runs, and 16 of them spent all 10 turns on malformed calls and ended empty. The cold arm already scored 0.917, so a ceiling would likely have hidden any gain even with every run parsed. We did not rerun with a longer turn cap.
- Two models, 16 fixtures, one synthetic deletion benchmark. Fixture is the unit of analysis, so the effective n for the E1 verdicts is 16.
- E4 and E7 are exploratory. E4 `hidden_pass` can be faked by a model that sets out to do so.
- E1H, the held-out set, did not run. Its gate needs P4a and P4b both SUPPORTED, and P4b is NOT SUPPORTED.
- One harness change followed the first look at the data: the report column "Read /protocol" now reads "Read a reference file (SKILL.md is already in the prompt)". Arms, fixtures, thresholds and scoring did not change.

## What it means for the product

The data supports enforcement over instruction. A PreToolUse gate with an audit ledger removes the harm the prose spec leaves in place, and the prose spec cannot claim to remove it. The spec keeps value as the contract the gate enforces and as the free format, since inlined references fix report structure. The claim to test next is a deletion-incident rate with a baseline on a model and tool chain a buyer already runs.
