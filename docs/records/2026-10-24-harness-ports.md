# Harness ports, 2026-10-24

The patton-gate hook now runs on eight more coding-agent harnesses, each proven by a $0 trace: the real harness,
headless in a podman container with no network, driven by a scripted model over a Unix-socket relay, against a
`/work` that holds one file the gate must protect. Every outcome is read from the files, the gate's state and the
model's requests, never from the harness's exit code. Each port's details, pinned download and checksum are in
`lab/patton_lab/harness/<name>/RESULT.md`; each trace was run once by the agent that wrote it and once more,
separately, before this record.

The shared pieces: `lab/patton_lab/harness/bridge.py` maps a harness's hook payload onto the plugin gate through a
per-harness dialect in `dialects/`, turns the gate's ask into a block (nobody answers headless), and blocks on any
error of its own. For harnesses whose hooks run in-process, a small extension spawns the bridge and blocks unless it
allows.

| Harness | Version | Hook | Required scenarios | Hook crashes | Hook hangs | Refusing a stop |
| --- | --- | --- | --- | --- | --- | --- |
| Pi | 1.4.0 | our TypeScript extension | S1 to S6 pass | blocked | blocked | works only with a message the model can read; no cap |
| oh-my-pi | 18.12.6 | our TypeScript extension | S1 to S6 pass | blocked | blocked | `block` has no cap; `continue` stops after 8 |
| OpenCode | 1.19.4 | our JavaScript plugin | S1 to S6 pass | blocked | blocked | no stop hook exists |
| DeepSeek harness (dsh) | 0.2.1-rc.3 | command hook through `dsh-hooks-claude-code` | S1 to S4 pass | **the call runs** | **the call runs** | works; no cap |
| goose | 1.56.0 | command hook plugin | S1 to S4 pass | blocked with `on_failure: block`, runs without it | the same | works; capped by `GOOSE_STOP_HOOK_BLOCK_CAP` |
| Cline CLI | 3.0.73 | `PreToolUse` file hook | S1 to S4 pass | **the call runs** | **the call runs** after a fixed 120 s | no stop hook exists |
| Open Interpreter | rust-v0.0.56 | command hook (Codex fork) | C0 and S1 to S4 pass | **the call runs** | **the call runs** | works; no cap (252 model requests in 45 s) |
| Gemini CLI | 0.65.0 | `BeforeTool` command hook | C0 and S1 to S4 pass | **the call runs** on exit 1; blocks on exit 3 with stderr | **the call runs** | works; stops asking the model after 16 turns but keeps calling the hook |

S1 is a read that must run, S2 a removal of the protected file that must be blocked with the gate's reason reaching
the model, S3 a new file that must be written, S4 an edit through the harness's own edit or write tool that must be
blocked; C0 is the same removal with no hook, which must succeed. Every trace was also checked to go red without the
gate.

## What the ports found

- **Four harnesses let a call run when their own command hook crashes or hangs:** dsh, Cline, Open Interpreter and
  Gemini CLI, and goose unless `on_failure: block` is set. The bridge blocks on its own errors, but a missing
  `python3`, a killed process or a hang get through on those hosts. Where the hook runs in-process (Pi, oh-my-pi,
  OpenCode), our extension fails closed.
- **A stop hook must cap itself.** Pi, oh-my-pi's `block` form, dsh and Open Interpreter repeat a refused stop with
  no limit, and Gemini CLI keeps calling the hook after it stops calling the model.
- **Cline's `cancel` ends the whole run** and its reason never reaches the model, so the port rewrites a refused
  command into one that prints the refusal and exits 1; a refused edit still ends the run.
- **Paths around the gate:** Open Interpreter's `write_stdin` (typing into a running shell) gets no hook, and the
  harnesses' MCP, subagent and app tools are not guarded by these ports.
- **Gemini CLI speaks only the Gemini wire format.** Real runs on Groq would need a translating proxy, which LiteLLM
  documents but which is untested here.
- **Network.** The earlier Codex runner's `slirp4netns:allow_host_loopback=true` also reaches the internet; these
  traces use `--network=none` and a Unix-socket relay instead.

## One accident

The Open Interpreter and Gemini CLI agent cleaned up with a glob that also removed the other agents' run
directories under `lab/harness_cache/`, which made one goose trace crash; the goose rerun passed in full. Those
directories are gitignored evidence that each trace recreates.
