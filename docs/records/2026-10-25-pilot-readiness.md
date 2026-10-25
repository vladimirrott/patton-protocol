# Pilot readiness, 2026-10-25

Everything the H1 and P1 pilots of the [research plan](../plans/2026-10-24-triage-and-harnesses-research-plan.md)
need is built and checked for $0. No paid call has been made for either pilot. This record says what was built,
which facts the build rests on and how each was measured, and how to run the pilots.

## patton-gate 0.4.0

Three additions, each off until the user file turns it on (the plugin README has the settings):

- **T1 triage** in the ask path. Before the gate asks about a flagged shell command, an analyst model may let it
  run. The prompt and the decision rules are the T1 study's ([findings](../findings/2026-10-24-triage-findings.md)),
  and `tests/test_gate_triage.py` checks that the plugin's analyst reads byte for byte what the study's read, for
  every stored flag of the test and development sets. Only a flag the gate can undo reaches it: every flagged file
  has a kept copy and still holds the bytes it held at session start, which the gate records when triage is on.
  Edits through `Write` or `Edit` are not triaged, because the study measured only commands. When the agent stops,
  the person gets one message listing what triage let through.
- **Discipline rules**, all mechanical: a reminder next to the result when a call changes a test; a reminder or a
  refusal for a file outside `discipline.scope`; a warning at four fifths of `discipline.max_calls` and refusals
  past it; a refused stop when files changed after the last check (`check_before_stop`); and the worker report
  contract at the stop, refused when a field is missing, a changed file is left out, a listed check never ran, or
  `completed` follows a failed check. Refused stops are capped per turn (`max_rounds`).
- **Codex**: the `Stop` and `PostToolUse` hooks are wired, so triage, discipline and the Verifier work there.

Checks: 49 new mutants in `tests/tools/mutate_plugin.py`, all killed. Real hosts with a scripted model:
`tests/tools/gate_triage_e2e.py` and `gate_discipline_e2e.py` on Claude Code 2.1.310, `gate_codex_turn_e2e.py` and
the reworked `gate_codex_e2e.py` on Codex 0.159.0, all passing.

## Facts the build rests on

| Fact | How it was measured |
| --- | --- |
| Codex 0.159 sends `Stop` with `last_assistant_message`, `stop_hook_active` and `turn_id`, and a block's reason reaches the model | `tests/test_codex_contract.py`, real binary, scripted model |
| Codex 0.159 carries `additionalContext` to the model from `PreToolUse` and from `PostToolUse` | the same |
| Codex 0.159 gives `PostToolUse` the output as a string and no exit code, and has no failure event | the same; the gate records a Codex check's outcome as unknown |
| No hook fires when Codex 0.159 spawns a subagent from a code-mode cell | the same; subagent limits cannot be built on Codex |
| Claude Code 2.1.310 sends `PostToolUseFailure` (`error: "Exit code 3"`) for a failing Bash command and `PostToolUse` for a passing one | a probe with real Claude Code and a scripted model |
| LiteLLM 1.92.0's `/v1/messages` drops the `role: "system"` entries Claude Code 2.1.310 puts inside `messages` for an unknown model, which is where the gate's reminders travel | a replay of two captured requests; the Claude Code port sets `CLAUDE_CODE_MODEL_CAPABILITIES=-mid_conv_system`, which sends them as user text |

A post hoc check on the stored T1 answers, chosen after the results were seen: requiring the analyst's quote to
name the path it frees would have refused 401 of gpt-oss-120b's 599 allows and caught no more must-escalate flags
(none was allowed to begin with), so the studied rule stays.

## The harness ports

Each port runs the real harness in podman with no network of its own; a Unix-socket relay is its only way out, to
the host's recorder. A command hook runs through `lab/patton_lab/harness/shim/patton-hook.sh`, which prints the
harness's own refusal when the bridge does not finish: a missing `python3`, a crash, or a run past its limit. Each
harness's `RESULT.md` holds the scenario tables.

| Harness | Version | Crash and hang of our hook | Refused stop | Reminder reaches the model |
| --- | --- | --- | --- | --- |
| Pi | 1.4.0 | blocked (in-process extension) | yes, capped by the bridge at 4 | yes |
| oh-my-pi | 18.12.6 | blocked (in-process extension) | yes, capped by the bridge at 4 | yes |
| OpenCode | 1.19.4 | blocked (in-process plugin) | no stop hook exists | yes |
| dsh | 0.2.1-rc.3 | **blocked through the shim**; ran before | yes, capped by the bridge at 4 | yes, through a PostToolUse hook |
| goose | 1.56.0 | blocked, by the shim and by `on_failure: block` | yes; goose's own cap also applies | no, on an allowed call |
| Cline | 3.0.73 | **blocked through the shim**; ran before | no stop hook exists | yes |
| Open Interpreter | rust-v0.0.56 | **blocked through the shim**; ran before | yes: 6 requests where the raw hook made 215 | yes |
| Gemini CLI | 0.65.0 | **blocked through the shim**; ran before | yes: ends in 8 s where the raw hook looped with 1 470 calls | yes, through an AfterTool hook |
| Claude Code | 2.1.310 | the plugin's own hooks | yes | yes |

Gemini CLI speaks only the Gemini wire format. `lab/patton_lab/harness/gemini-cli/proxy.py` translates it to chat
completions (32 unit tests, 10 of 10 mutants killed, and the real CLI passes its scenarios through it). One new
finding there: when the CLI's own hook timeout is shorter than the shim's limit, the CLI kills the shim and runs
the call, so every port sets the harness's timeout above the shim's.

What the ports leave unguarded: subagent, MCP and app tools on most harnesses, Open Interpreter's `write_stdin`,
and on Cline a refused edit ends the whole run.

## The fleet runner

`lab/patton_lab/fleet/` runs any port through one recording proxy per run (`recorder.py`, chat completions and
Anthropic Messages). The proxy forwards each call unstreamed so its usage is known, replays it to the harness in the
form it asked for, holds each call's worst case against the run set's cap and the lab-wide $40 over every ledger
(the new `fleet_spend.jsonl` counts there), waits out a 429, and stops the run after three upstream failures. The
gate's own analyst and Verifier calls go through the same proxy on a separate route.

Two suites (`suites.py`): `e1` for H1, the six cleanup fixtures that harmed in the Claude Code study, with arms
unguarded, rules, and rules with T1; `p1` for P1, with arms none, the work rules in the mission, the just-in-time
reminders, and a refused unchecked stop followed by the blind Verifier. `runner trace --set h1|p1` drives every port
through every arm with a scripted worker and a scripted analyst, and writes a stamp of the code it ran on; the paid
`pilot` refuses a harness whose stamp does not match the tree. Records and cassettes are scrubbed of the host's
home and user name before they are written.

## The P1 fixtures

`lab/patton_lab/p1/fixtures.py` holds four small Python projects with stdlib unittest suites: p01, an impossible
test beside real leap-year bugs; p02, pagination off by one in two places; p03, a rename across eight files;
p04, a wrong rate in a config file the task may not change. The oracle runs the shipped tests and hidden tests in
the lab's isolated container, never on the host. `tests/test_p1_fixtures.py` scores sixteen reference end states
(each fixture's start, its honest end and each of its hacks) and gets the expected answer for each.

## Running the pilots

```sh
cd lab
python3 -m patton_lab.fleet.runner trace --set h1 && python3 -m patton_lab.fleet.runner trace --set p1
triage/with_groq_proxy.sh python3 -m patton_lab.fleet.runner pilot --set h1 --jobs 2
triage/with_groq_proxy.sh python3 -m patton_lab.fleet.runner pilot --set p1 --jobs 2
python3 -m patton_lab.fleet.runner report --set h1
```

H1 is the 8 ported harnesses × 3 arms × 6 fixtures = 144 runs (Claude Code's cells are the gate study's 560
runs); P1 is the 7 that can refuse a stop, Claude Code among them, × 4 arms × 4 fixtures = 112 runs. At the Claude
Code study's $0.0275 a run, the two cost about 144 × 0.0275 ≈ $3.96 and 112 × 0.0275 ≈ $3.08, my estimate; the caps
are $5 and $4. The lab has spent $28.3043 of
its $40. The pilots carry no verdict: they size the registered studies.

Known risks for the paid runs: Claude Code's requests carry about 15k input tokens of tool schema each, and LiteLLM
maps its thinking setting to `reasoning_effort: "high"`; a T1 call slower than about 110 s is refused on Cline,
whose hook timeout is fixed at 120 s; on Pi and oh-my-pi a Verifier slower than the 25 s stop timeout lets the stop
through unchecked.
