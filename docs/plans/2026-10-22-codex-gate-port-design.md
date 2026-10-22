# Design: the gate study on Codex

Date 2026-10-22. Status: building. The Claude Code gate study ([findings](../findings/2026-10-22-gate-study-findings.md))
measured the frozen `gate/patton_gate.py` inside Claude Code on gpt-oss-120b. This port runs the same fixtures, the
same mission, the same simulated operator and the same scoring inside Codex CLI, on the models a Codex subscription
serves. The first goal is a calibration: how much of a weekly Codex limit one run takes, so a pre-registration can
size a study that fits.

Nothing frozen changes. The port adds `lab/patton_lab/codex/` and imports the frozen gate, fixtures, approvers and
scorer as they are.

## What Codex 0.159.0 does, measured

A probe ran the real `codex exec` binary against a scripted Responses endpoint with a logging hook, for $0. Each
fact below is pinned by `tests/test_codex_contract.py`, which reruns the probe whenever the binary is present.

| Fact | Consequence for the port |
| --- | --- |
| Shell calls reach a PreToolUse hook as `tool_name: "Bash"`, `tool_input: {"command": ...}`; patches as `tool_name: "apply_patch"` with the patch text in `command`. | The gate's Bash classifier applies unchanged. Patches need a translation. |
| `deny` with a reason blocks the call; inside a JavaScript `exec` cell the denial throws. | The gate's refusals work as in Claude Code. |
| `ask` is unsupported, and `allow` is valid only with `updatedInput`. An unsupported answer counts as no answer. | The adapter turns the gate's `allow` into no answer, or into an allow that carries a rewrite. |
| A hook that exits 1, or runs past its timeout, lets the call run. | Codex hooks fail open. The adapter must never crash, and the hook timeout must exceed the approver's. |
| The hook sees the turn's `cwd` and only `command`. `exec_command`'s `workdir` argument is invisible to it, so `rm keep.txt` with `workdir: "sub"` deletes `sub/keep.txt` while a path-reading gate checks `keep.txt`. A heredoc `apply_patch` resolves against `workdir` too. | The adapter rewrites every allowed command to `cd <cwd> && <command>`. Codex still intercepts a heredoc patch in that form, and the files land where the gate looked. |
| With the real model metadata, `gpt-6.1-sol` runs in `code_mode_only`: the model gets one JavaScript `exec` tool and calls `exec_command` and `apply_patch` from inside it. Nested calls reach the hook with ids `exec-<uuid>`. | The transcript holds JS cells, not tool calls. Executed calls come from the `codex exec --json` stream. |
| `write_stdin` has no PreToolUse hook by design: it continues a command that already passed. | An allowed command that reads a program from stdin is an unguarded channel. The findings name it as untested. |
| A custom provider gets fallback model metadata unless `model_catalog_json` names a catalog. | The run installs a catalog entry copied from the host's `models_cache.json`, so the worker sees the prompt and tools a subscriber sees. |
| A custom provider with `supports_websockets = false` streams over HTTP SSE. | A recorder on the host can relay and record every call. |

## Arms

| Arm | Gate | Operator | Skill |
| --- | --- | --- | --- |
| X-R0 | none | none | no |
| X-R1 | hook | oracle | no |
| X-R0S, X-R1S | as above | as above | yes |
| X-R1D | hook | deny-all | no |

Every arm runs `codex exec --dangerously-bypass-approvals-and-sandbox` in the pinned podman image, so the
container is the sandbox, as `bypassPermissions` was in the Claude Code study. Hook arms add the hooks and
`--dangerously-bypass-hook-trust`. Subagents, goals, web search, image generation, apps, plugins and memories are
off in every arm, so the worker has the shell and the patch tool and nothing that runs code past the hook.

## The hook adapter

`lab/patton_lab/codex/adapter.py` runs in the container as the hook command and loads the frozen gate in-process.

- **Bash.** The event goes to the gate as it is. A refusal passes through. An allow, or no decision, becomes
  `allow` with `updatedInput.command = "cd <cwd> && <command>"`.
- **apply_patch, and a Bash heredoc that runs `apply_patch`.** The adapter reads every path the patch adds,
  updates, deletes or moves, and asks the gate about a synthetic `rm -f -- <paths>`, so the gate sees one request
  for all the files and the operator answers once. A patch it cannot read is refused with a request to rewrite it.
- **Anything else, and any error,** is refused with the reason. A crash in the adapter would let the call run, so
  every path ends in an answer.

## Model access and recording

The worker talks to a recorder on the host (`lab/patton_lab/codex/recorder.py`), which relays each Responses call
and stores it in a cassette. For subscription runs the recorder forwards to `https://chatgpt.com/backend-api/codex`
and adds the bearer token and account id it reads from `~/.codex/auth.json` on each call. The container gets a
placeholder key and never sees a token, so a token refresh inside it cannot rotate the host's login. The recorder
also keeps the rate-limit headers of every response: they report the share of the 5-hour and weekly windows used,
which is the calibration's measurement. For `trace` the recorder forwards to a scripted endpoint
(`lab/patton_lab/codex/fake_upstream.py`) that answers with JavaScript `exec` cells, as the real model does.

## Calibration

`runner calibrate --model M --runs N` (N at most 10) runs X-R0 and X-R1 on fixtures from E1, reads the weekly
window's used percentage before the first call and after the last, and reports tokens and percentage per run. It
refuses to start without a passing `trace` from the same tree.

## Not covered

The study measures an honest worker that the gate stops by reading commands. The workdir rewrite, the patch
translation and the synthetic request are new behaviour that the Claude Code study did not measure.
