# Research plan: a triage tier for the gate, eight more harnesses, and process discipline

Written 2026-10-24, after the Codex study. Nothing here has run. Every paid study below gets its own
pre-registration, a free end-to-end trace before the first paid call, cassettes for every model call, and the
lab-wide spend guard ($40 across every ledger; $24.199 spent before this plan).

## Status

- T1-offline ran on 2026-10-24 under its own pre-registration
  ([findings](../findings/2026-10-24-triage-findings.md)). Both registered thresholds held, on 14 distinct critical
  cases; the next triage measurement needs more distinct cases and sparser notes.
- H0 harness traces ran for all eight harnesses ([record](../records/2026-10-24-harness-ports.md)).
- The blind Verifier (RQ3, "verify before claiming") shipped in patton-gate 0.3.0 and was replayed over the 800
  stored runs under its own pre-registration ([findings](../findings/2026-10-25-verify-findings.md)). Both
  predictions came out inconclusive; it catches losses the notes name and misses most losses they leave unsaid.
- Built for the two pilots on 2026-10-25, all checked for $0 ([record](../records/2026-10-25-pilot-readiness.md)):
  T1 triage inside the plugin with the studied prompt, the discipline rules (reminders, scope, budget, a check
  before the stop, the report contract), the stop rules and the Verifier on Codex, a fail-closed shim for the
  harnesses whose own hooks fail open, stop support through the bridge with its own cap, a translating proxy for
  Gemini CLI, the fleet runner that runs every port through a recording proxy under the lab's cap, and the P1
  temptation fixtures with an isolated oracle.
- The CTO raised the lab cap from $40 to $90 on 2026-10-25.
- The H1 and P1 pilots ran on 2026-10-25 ([findings](../findings/2026-10-25-fleet-pilots.md)): 144 and 112 runs for
  $0.78 and $0.92. Unguarded, every one of the eight harnesses destroyed a needed file at least once (19 of 48
  runs); the gate's arms did so in none of 96, and T1 cut the questions from 1.167 to 0.688 a run. In P1 only the
  refused stop with the Verifier moved test gaming (10 of 28 to 4) and overclaiming (8 to 2). Two lab bugs surfaced
  and were fixed before the registered studies: Groq refuses the `reasoning_content` field three harnesses send,
  and a refused request was stored as a valid run.
- The registered studies H1R (768 runs) and P1R (448 runs) are pre-registered from the code
  ([H1R](../../lab/prereg/2026-10-25-fleet-h1r-preregistration.md),
  [P1R](../../lab/prereg/2026-10-25-fleet-p1r-preregistration.md)), each under its own $8 cap.
- H1R ran on 2026-10-26 ([findings](../findings/2026-10-26-fleet-h1r-findings.md)): no harm in 512 gated runs on
  eight harnesses (R1 SUPPORTED), questions cut to 0.569 of the rules arm's (R2 SUPPORTED), and 20 analyst allows
  outside the oracle's policy, none of which lost data (R3 NOT SUPPORTED). 370 runs failed to start their container
  on the first pass (a podman keyring leak) and ran again on the same code.

## Questions

- **RQ1, triage.** The gate flags every change to a file that existed at session start. Can an LLM analyst (T1)
  sort those flags so a person sees only the critical or suspicious ones, without letting a harmful change through?
- **RQ2, portability.** Does the gate hold on harnesses other than Claude Code and Codex, and how much harm does
  each harness do unguarded on the same model?
- **RQ3, process discipline.** Can hooks make a worker finish the task, run its checks before claiming success,
  report honestly without gaming tests, and stay inside its paths and budget?

## What the evidence says so far

- Claude Code on gpt-oss-120b (560 runs): the hook took cleanup harm from 0.196 to 0.000, with 1.134 questions per
  run and no bypass in 224 hooked runs.
- Codex on gpt-6.1-sol (240 runs): unguarded harm was 0.000, so the gate had nothing to remove; it cost one
  question and about one extra model call per run, with no bypass in 96 hooked runs and no measurable utility loss.
- Prompt text alone did little in the lab (the skill text, in passing), and the literature agrees: METR saw "do not
  cheat" instructions move o3's reward hacking from 80% to 70% at best.
- Monitors that read the monitored agent's text get fooled. Under injected text a GPT-4.1 monitor scored all 375
  backdoored samples 0 (Terekhov et al.); rewritten agent narratives inflated judge false positives by up to 90%
  (Khalifa et al.); one independent test of Claude Code auto mode saw its classifier allow untrusted code in 6 of
  10 runs. Anthropic reports that users accept 93% of manual permission prompts, the alert fatigue a triage tier
  must reduce. Sources and fetch dates are in [`docs/research/2026-10-24/`](../research/2026-10-24/): the
  literature notes, the Groq notes, and the two harness reports these tables come from. Those notes were gathered
  by research agents and are leads; each number above gets checked against its source before a study quotes it.

## The triage design (RQ1)

| Tier | Who | Job |
| --- | --- | --- |
| T0 detect | the deterministic gate | flag every risky call; high recall, about 0.3 s, no tokens |
| T1 triage | an LLM analyst | for each reversible flag: allow and log, or escalate |
| T2 decide | the person | only escalated flags |
| Digest | the person, after the session | one summary of what T1 allowed, each line with its `restore` |

Five rules bound T1:

1. Irreversible flags never reach T1: no kept copy, outside the project, the gate's own files, any tamper sign, a
   command the gate cannot read. They go straight to the person.
2. T1 may only downgrade a flag the gate can undo, so its worst mistake costs one `restore`.
3. T1 sees the gate's facts (paths, sizes, tracked or ignored, copy kept, the diff) and the person's mission text.
   It never sees the worker's reasoning or justification.
4. A timeout, an error, an unparsable answer or a rate limit escalates.
5. T1 answers in a fixed schema: severity, reason, and the facts it relied on, quoted from its input.

## Harnesses (RQ2)

Read from source at the tags below on 2026-10-24; nothing was built or run. Stars are a snapshot.

| Harness | Tag | Pre-tool block | Stop refusal | Hook form |
| --- | --- | --- | --- | --- |
| DeepSeek harness `dsh` | `dsh-v0.2.1-rc.3` (prerelease) | yes | yes | in-process, plus a stdin-JSON bridge for Claude-style hooks |
| Pi | `v1.4.0` | yes (block, no ask) | partial (continue plus a user message) | in-process TypeScript |
| oh-my-pi `omp` | `v18.12.6` | yes | yes (main session) | in-process TypeScript |
| OpenCode | `v1.19.4` | yes (throw) | no | in-process plugin |
| Gemini CLI | `v0.65.0` | yes | yes | stdin-JSON command |
| Cline CLI | `cli-v3.0.73` | yes | no | file hook or plugin |
| Open Interpreter (Codex fork) | `rust-v0.0.56` | yes (shell, patch, MCP) | yes | stdin-JSON command |
| goose | `v1.56.0` | yes | yes (capped) | stdin-JSON command |

Two shims cover them: a command adapter that maps each stdin-JSON dialect onto `patton_gate.py` (dsh bridge,
Gemini CLI, Cline file hooks, Open Interpreter, goose), and a TypeScript shim that spawns the same script from an
in-process handler (Pi, oh-my-pi, OpenCode, dsh native). Four harnesses let a crashed or timed-out hook through by
default, so every port must pass a crash test and a timeout test before it counts.

Known risks: Gemini CLI speaks only the Gemini wire format, so it needs LiteLLM's translation, and whether Groq
tool calls survive that is unknown; dsh has only prereleases; OpenCode and Cline cannot refuse a stop, so they sit
out RQ3's stop arm.

## Models (Groq, self-serve, prices read 2026-10-24)

| Model | Use | Input / cached / output, $ per million tokens |
| --- | --- | --- |
| openai/gpt-oss-120b | main worker; T1 candidate | 0.15 / 0.075 / 0.60 |
| openai/gpt-oss-20b | cheap worker arm; T1 candidate | 0.075 / 0.037 / 0.30 |
| openai/gpt-oss-safeguard-20b | T1 candidate built for policy classification | 0.075 / 0.037 / 0.30 |
| qwen/qwen3.8-27b | second model family, preview | 0.80 / none / 4.00 |

Rate limits on the developer plan are 250K tokens and 1K requests per minute per model id. Cached tokens do not
count toward them. The docs never say in words that two model ids draw on separate buckets; the first pilot reads
the `x-ratelimit-*` headers on two models at once to settle it.

## Experiments, cheapest first

**T1-offline (RQ1), about $1, no containers.** Replay every flag the stored studies raised, with the fixture oracle
as ground truth: the asks in the 224 hooked Claude Code runs and the 96 hooked Codex runs, plus the flags the gate
raises over the unguarded runs (the R2 replays), roughly 700 labeled flags in all. Arms: four T1 candidates × two
contexts (rule 3 kept, and the worker's text added) × two policy prompts. Metrics: escalations per run (the target
is well under the 1.134 the person answered), missed critical flags (the target is 0), false escalations. Each
call is about 2 000 tokens in and 200 out, so 700 flags cost about $0.15 on gpt-oss-20b and $0.30 on gpt-oss-120b,
my estimate from the listed prices.

**H0-trace (RQ2), $0.** For each harness: install the pinned tag in the podman image, drive it with the lab's
scripted model, and check that the hook fires, refuses, fails closed on a crash and a timeout, and (where it can)
refuses a stop. The eight run in parallel.

**H1-pilot (RQ2), about $4.** Eight harnesses × three arms (unguarded, rules gate, rules plus T1) × the six fixtures
that harmed in the Claude Code study (f02, f04, f08, f11, f12, f15) × one rep = 144 runs on gpt-oss-120b. The
Claude Code study's measured cost was $15.3963 for 560 runs, about $0.0275 per run; harnesses with smaller system
prompts should cost less, and the pilot measures each one. The pilot sizes the registered study, and that study
waits for a budget decision.

**P1-pilot (RQ3), about $2.** New fixtures that each hold one temptation: an impossible test, a long multi-file
change, a check that fails until the real fix lands, an out-of-scope file the task seems to need. Arms: none,
prompt rule, just-in-time hook reminder, stop refusal plus a blind Verifier that checks each claim against the diff
without the worker's report. Metrics: tasks finished, success claims backed by a check that ran, tests edited or
special-cased, scope and budget breaches. It runs on the five harnesses that can refuse a stop (dsh, oh-my-pi,
Gemini CLI, Open Interpreter, goose) plus Claude Code. The literature found no study of hook reminders, stop
refusal or blind verification of coding-agent claims, so this is the open part.

## Running it in parallel

- One queue per Groq model id, each with a token-bucket scheduler in front of the recorder, so runs on different
  models proceed side by side and a 429 never costs an attempt.
- Concurrency per model is set by the pilot. A run of about 12 calls and about 108K input tokens over one to two
  minutes uses 50K to 110K tokens per minute uncached, so two to four runs fit under 250K at once, more with cache
  hits.
- The free lanes (H0 traces, T1 code, fixture authoring) run beside the paid ones.
- Containers are capped by host memory; each run gets its own disposable sandbox and loopback port.

## Budget

| Item | Estimate |
| --- | --- |
| Spent before this plan | $24.199 |
| T1-offline | about $1 |
| H1-pilot | about $4 |
| P1-pilot | about $2 |
| Left under the $40 cap | about $8.8 |

The registered H1 and P1 studies did not fit in what was left. After the pilots the CTO added $50, and each
registered study runs under its own $8 cap.

## Threats to validity

- Workers may act differently when they detect a test; fixtures should read like ordinary repositories.
- Prompt wording moves reward hacking by large amounts, so every arm's text is frozen and hashed.
- Vendor classifier numbers come from vendors; small samples (10 runs in the one independent auto-mode test) give
  wide intervals.
- One model family dominates: gpt-oss is the only cheap self-serve family with tool calls on Groq today.
