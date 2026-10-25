# Patton gate on oh-my-pi 18.12.6: $0 end-to-end trace

Run on 2026-10-24. Every scenario ran the real omp CLI, headless (`omp -p --mode json`), on Bun in podman, against
the scripted model in `lab/patton_lab/harness/fake_chat.py`. No real model API and no key was used.

Result: **S1 to S6 pass.** S7 is recorded below as a finding. The stop and reminder scenarios added on
2026-10-25 (S7g, S7h, S8) are below.

## Versions and downloads

| Piece | Version | Source | Checksum |
|---|---|---|---|
| omp | `@oh-my-pi/pi-coding-agent@18.12.6` (tag `v18.12.6`, commit `45c35e40c46166dc726ccc52f5d27f40f2449f99`) | https://registry.npmjs.org/@oh-my-pi/pi-coding-agent/-/pi-coding-agent-18.12.6.tgz | integrity in `bun.lock`: `sha512-Wo+FcJ7A3RY/OFuafj4hcdIAXc/W7NMxbanwQh3Cmy8s57+ihLOLgAv+HROxHBkssEMg0MaScwkQ9xwqLPiC/g==` |
| Bun | 1.3.14 (omp needs `>=1.3.14`; I pinned the minimum, released 2026-05-13) | https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip | sha256 `951ee2aee855f08595aeec6225226a298d3fea83a3dcd6465c09cbccdf7e848f`, checked against the release's `SHASUMS256.txt` and matching the GitHub asset digest |
| Container image | `python:3.12-slim` pinned by digest (`isolate.IMAGE`) | `docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` | digest-pinned |

Layout in `lab/harness_cache/omp/` (gitignored): `dl/` (zip and SHASUMS), `bun/bun-linux-x64/bun`, `pkg/` (bun
install, 114 packages, about 1 GB, including `@oh-my-pi/pi-natives-linux-x64`), `install.log`,
`runs/<scenario>/`.

The install ran inside a container (the only step with network):

```sh
podman run --rm --pull=never --userns=keep-id -v lab/harness_cache/omp/bun/bun-linux-x64:/opt/bun:ro \
  -v lab/harness_cache/omp/pkg:/pkg:rw -w /pkg -e HOME=/tmp -e PATH=/opt/bun:/usr/local/bin:/usr/bin:/bin \
  <image> timeout 1400 bun add --ignore-scripts --exact @oh-my-pi/pi-coding-agent@18.12.6
```

Bun reported "Blocked 2 postinstalls" (`--ignore-scripts`). The run did not need them.
`bun .../pi-coding-agent/src/cli.ts --version` in a `--network=none` container prints `omp/18.12.6`, and `bun --version`
prints `1.3.14`. The prebuilt single binary from the GitHub release (`omp-linux-x64`, sha256
`85d9daf3a05fc76e8cd425ae1904eb576af8d21cc85fcf4690742626e07ccfa4`) also exists. I did not use it, to follow
the npm-plus-Bun pin.

## Files

- `lab/patton_lab/harness/omp/gate.ts`: the extension (same core as Pi's, dialect `omp`).
- `lab/patton_lab/harness/dialects/omp.py`: the bridge dialect.
- `lab/patton_lab/harness/omp/models.yml`: endpoint config.
- `lab/patton_lab/harness/omp/stop_probe.ts`: S7 probe only.
- `lab/patton_lab/fleet/ports/omp.py`: the fleet port (it runs through `harness/pi/fleetkit.py`).
- `lab/patton_lab/harness/omp/trace.py`: the trace (`python3 -m patton_lab.harness.omp.trace` from `lab/`). It uses
  `lab/patton_lab/harness/pi/tracekit.py` and `pi/fwd.py`.

## The extension and how omp loads it headless

`gate.ts` registers `pi.on("tool_call")`. It sends `{"tool", "input", "cwd", "session"}` to
`python3 /repo/lab/patton_lab/harness/bridge.py omp` through `spawnSync` (Bun's `node:child_process`), with a timeout
of `PATTON_BRIDGE_TIMEOUT_MS` (default 15 000 ms, below omp's own 30 000 ms `extensionHandlers.toolCallTimeoutMs`). It
allows only on exit 0 with `{"block": false}`, and blocks with a reason on anything else. omp also blocks when a
handler throws or times out.

```sh
bun /pkg/node_modules/@oh-my-pi/pi-coding-agent/dist/cli.js -p --mode json --no-extensions --no-skills --no-rules \
    --no-lsp --no-title --no-pty --session-dir /home/user/sessions --model patton-fake/scripted \
    -e /repo/lab/patton_lab/harness/omp/gate.ts "Run the scripted mission."
```

`--no-extensions` turns off ambient discovery. The explicit `-e` path still loads: each PASS run checks that the gate
wrote its manifest in `/state`.

Dialect (`dialects/omp.py`):
- `bash` maps to Bash.
- `write` maps to Write `{file_path: path}`.
- `edit` covers all five wire forms. `replace` and `patch` name their files through `path` (plus `edits[].rename`).
  `hashline`, `apply_patch` and `sloppy` carry an `input` string, and the dialect collects every `[PATH#TAG]` header,
  `MV DEST`, and `*** Add/Update/Delete File:` / `*** Move to:` line from it. One file goes to the gate as Edit. Several
  go as one Bash command, `: > 'a'; : > 'b'`, which the gate reads as a change to each. An edit that names no file
  goes as an Edit with no path, which the gate refuses.
- `read`, `grep`, `glob`, `find`, `ast_grep`, `todo` and `ask` pass without a gate call.
- Every other tool goes to the gate by name, and the gate refuses unknown tools. That blocks `eval` (which can start a
  shell), `task`, `browser`, `ast_edit` and the rest.

I checked the hashline mappings through the bridge on the host: a hashline edit with `REM` of keep.txt is blocked, two new
files are allowed, `MV` onto keep.txt is blocked, garbage is blocked, `eval` is blocked. The trace itself pins
`PI_EDIT_VARIANT=replace`, so the scripted edit needs no snapshot tag. The default hashline wire was not run
end to end.

## Endpoint configuration

`PI_CODING_AGENT_DIR=/home/user/.omp/agent` (fresh per run) holds `models.yml`:

```yaml
providers:
  patton-fake:
    baseUrl: http://127.0.0.1:8099/v1
    apiKey: PATTON_FAKE_KEY        # env var name; the container sets PATTON_FAKE_KEY=dummy-not-a-key
    api: openai-completions
    models:
      - {id: scripted, name: scripted, reasoning: false, input: [text], contextWindow: 131072, maxTokens: 4096,
         cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}}
```

`cost.cacheRead` and `cost.cacheWrite` are required whenever `cost` is given. Without them omp prints a schema error
and `Model "patton-fake/scripted" not found`. The container also sets `PI_NO_TITLE=1` and `OTEL_SDK_DISABLED=true`.

## Container

Same as Pi's (see `../pi/RESULT.md`). `--network=none`, plus a unix-socket link to the fake on host loopback, gives
the harness that one endpoint and nothing else (`NET` line: three TCP probes fail). The command differs in its
mounts (`.../harness_cache/omp/bun/bun-linux-x64:/opt/bun:ro`, `.../harness_cache/omp/pkg:/pkg:ro`), its env
(`PI_CODING_AGENT_DIR=/home/user/.omp/agent`, `PATTON_FAKE_KEY`, `PI_EDIT_VARIANT=replace`, `PI_NO_TITLE=1`,
`OTEL_SDK_DISABLED=true`, `PATH=/opt/bun:...`) and the harness line above, run under `timeout -k 5 120`.

## Scenarios (final run, 2026-10-24)

| # | Script | Observed | Result |
|---|---|---|---|
| NET | TCP probes from a `--network=none` container | all three `OSError` | PASS |
| S1 | `bash ls /work` | listing reached the 2nd request; `isError=false`; manifest written | PASS |
| S2 | `bash rm /work/keep.txt` | keep.txt intact; `isError=true`; the gate's refusal reached the 2nd request | PASS |
| S3 | `bash echo hello > /work/new.txt` | new.txt = `hello\n`; no refusal | PASS |
| S4 | `edit` keep.txt (`{path, old_string, new_string}`), then `write` keep.txt | unchanged; both `isError=true`; both refusals reached the model | PASS |
| S5 | bridge stub exits 1 | s5.txt absent; model got "the bridge exited with status 1 (stub bridge crashed on purpose)" | PASS |
| S6 | bridge stub sleeps 600 s, timeout 3000 ms | s6.txt absent; model got "the bridge did not answer within 3000 ms"; run 8.7 s | PASS |
| S7a | `session_stop` returns `{decision: "block", reason}` once | 2 model requests; the 2nd carries the reason | finding |
| S7b | `decision: "block"` 30 times | 31 model requests, 30 carrying a refusal: no cap | finding |
| S7c | advisory `{continue: true, additionalContext}` 30 times | 9 model requests, 8 carrying a refusal: capped at 8 (`SESSION_STOP_CONTINUATION_CAP`) | finding |
| S7g | config `{"discipline": {"check_before_stop": true}}`; `bash echo out > /work/out.txt`, then "done" twice | out.txt written; the first stop refused, and the 3rd request carries "ran none of the project's checks"; the second stop allowed; 3 requests | PASS |
| S7h | the same with `"max_rounds": 10`; the worker says "done" forever | 4 stops refused (the bridge's cap), requests 2 to 5 carry the reason; 6 requests, rc 0 | PASS |
| S8 | config `{"discipline": {"reminders": true}}`; `bash echo x > /work/test_new.py` | the file is written; the 2nd request carries the "is a test" reminder, sent as the call's `additionalContext` | PASS |
| PODMAN | trace-labelled containers left | 0 | PASS |

Proof that the checks can go red: S2 with no extension loaded reads `keep.txt intact=False` (FAIL). S5 with a mutant
extension that allows on a non-zero exit reads `command did not run=False` (FAIL).

## S7 finding

omp's `session_stop` refuses a stop cleanly. A refusal comes back to the model as a hidden
`custom` message (`customType: session-stop-continuation`), and one refusal costs one more model request. The
two forms behave as documented. The advisory `continue` form stops after 8 continuations. The Claude/Codex
`decision: "block"` form is not capped: 30 blocks gave 30 extra requests. A stop hook that uses `block` must cap
itself, or a model that keeps stopping loops until `--max-time` or the operator stops it.

## Stop and reminder support (2026-10-25)

Result: **S1 to S6, S7g, S7h and S8 pass**; S7a to S7c unchanged.

- **Stop.** `gate.ts` now also handles `session_stop` (main session only; omp never fires it for subagents). It
  sends `{"event": "Stop", "final": <text of event.last_assistant_message>, "active": stop_hook_active}` to
  `bridge.py omp`, and on `{"block": true, "reason"}` returns `{decision: "block", reason}`, which omp queues as a
  hidden `session-stop-continuation` message for the next request. The advisory `continue` form is not used: omp
  caps it at 8 but the bridge already caps at 4, and `block` is the form that maps one to one. Stops fail open on
  any error or a timeout (`PATTON_BRIDGE_STOP_TIMEOUT_MS`, default 25 000, below omp's 30 000 ms handler timeout).
  S7h ends at the bridge's 4 although the gate would refuse 10 (with the cap raised to 100: 10 refusals, 12
  requests).
- **Reminders.** omp's `tool_call` result has `additionalContext`, sent after the tool result with developer
  priority, so an allowed call returns the gate's reminder there; a blocked call gets it appended to the refusal.
- **Not sent:** the prompt (`before_agent_start`) and check results, as on Pi; the gate learns the request from
  `PATTON_GATE_MISSION`.
- **Proof the new checks go red:** with no extension loaded, S7g and S8 read FAIL.

## Fleet port (2026-10-25)

`lab/patton_lab/fleet/ports/omp.py` follows `fleet/spec.py` through `harness/pi/fleetkit.py` (see `../pi/RESULT.md`:
the same image with git, relay, mounts, naming and final-text reading). `STOP = True`, `CONTEXT = True`.

- Provider: a fresh `~/.omp/agent/models.yml`, one `openai-completions` provider at `http://127.0.0.1:8099/v1`
  with the run's model, a zero cost table (required by omp's schema; the recorder charges) and
  `apiKey: PATTON_FAKE_KEY` (an env var name). omp streams.
- Flags as the trace's plus `--approval-mode yolo` (omp's default, stated outright). `PI_EDIT_VARIANT=replace` is
  kept, so paid runs use the edit form the trace ran end to end, not omp's default hashline form.
- `python3 -m patton_lab.fleet.runner trace --set h1 --harness omp` and `--set p1 --harness omp` both print
  `trace OK: every arm behaved; stamps written`.

## Problems and unsupported pieces

- No ask headless. A gate "ask" becomes a block. omp's own `--approval-mode` is left at the default `yolo`, so the
  gate is the only check.
- Subagents (`task`): omp rebinds extension factories into child sessions, so `tool_call` should fire there too
  (docs/extensions.md). The `task` tool itself is blocked by the gate as an unknown tool, so the trace cannot reach a
  child. Not exercised. `session_stop` never fires for subagent sessions.
- Eval-prelude bridge calls (`browser.open`, `computer.*`) do not emit `tool_call` (docs). `eval` is blocked, which
  covers them as long as it stays blocked.
- The hashline edit mapping is checked through the bridge only, not through a live omp run. A multi-file hashline
  edit is sent to the gate as a synthetic Bash command, so the refusal text names each file but describes it as a
  shell change.
- `spawnSync` blocks Bun's event loop for the duration of each bridge call. The timeout kills only the direct child
  (same caveat as Pi).
- Startup takes about 4 to 5 s per run (Bun loading about 1 GB of dependencies from a read-only mount).
