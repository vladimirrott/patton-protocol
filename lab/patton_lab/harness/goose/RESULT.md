# goose: patton gate port, $0 trace

Run on 2026-10-24 and again on 2026-10-25 (shim, stop support, fleet port) against a scripted model
(`patton_lab/harness/fake_chat.py`); no real model, no API key.

## Version and download

- goose **1.56.0** (tag `v1.56.0`, commit `9949cf926cd42f1d128b9991ed23a496ecace896`). `goose --version` inside the
  container prints ` 1.56.0`.
- Asset: https://github.com/aaif-goose/goose/releases/download/v1.56.0/goose-x86_64-unknown-linux-gnu.tar.bz2
- sha256 `e29544421a6e698aab0fe4becda96096218ff36abe85b83785519d41c555c714`. It matches the digest GitHub publishes
  for the release asset (`gh api repos/aaif-goose/goose/releases/tags/v1.56.0`, field `digest`). goose publishes no
  separate checksum file. `trace.py` `fetch()` downloads into `lab/harness_cache/goose/dl/`, refuses any other
  digest, and unpacks the single `goose` binary into `lab/harness_cache/goose/bin/`.
- The binary runs only in the pinned lab image (`isolate.IMAGE`, Debian 13, glibc 2.41, Python 3.12).

## Dialect

`patton_lab/harness/dialects/goose.py`. goose cannot reuse `claude`: its payload names the event `event`, the cwd
`working_dir`, and its tools are `shell {command}`, `write {path, content}`, `edit {path, before, after}`. The
dialect maps those to the gate's `Bash {command}`, `Write {file_path, content}`, `Edit {file_path, old_string,
new_string}`, and returns None (allow) for every other tool and event. A block is `{"decision":"block","reason":...}`
on stdout with exit 0; an error is exit 2 (goose reads exit 2 as a block with stderr as the reason).

- **Stop.** goose sends `{"event": "Stop", "session_id", "last_assistant_message", "working_dir"}`; the message is
  the user-visible text of the trailing assistant messages, omitted when empty
  (`crates/goose/src/agents/state_machine/ops_stop_hook.rs` `trailing_assistant_text`). `emit_stop` answers with
  the same block form, which goose turns into a hidden user message, ``Stop hook `patton-gate` blocked ending this
  turn: <reason> ...``.
- **Reminders: goose cannot carry them on an allowed call.** goose reads only `decision` and `reason` from a hook
  (`crates/goose/src/hooks/mod.rs` `classify_output`); the only other output field, `banner`, comes from SessionStart
  and is shown to the person, not the model. `emit_context` puts the reminder in a refused call's reason; on an
  allowed call it is lost (S8). The fleet port says so with `CONTEXT = False`.

Unit tests: `python3 -m unittest patton_lab.harness.goose.test_dialect` (6 pass) and `tests/test_port_goose.py` (8
pass: stop parse, the bridge's cap in goose's form, the report rule reading the final message, emit_stop and
emit_context, the stream-json reader, the fleet port with its launch stubbed).

## Hook config

A goose plugin, `patton_lab/harness/goose/plugin/patton-gate/` (`plugin.json`, `hooks/hooks.json`), copied per run
and mounted read-only at `~/.agents/plugins/patton-gate` (goose's user plugin dir,
`crates/goose/src/config/paths.rs`). goose enables a newly found plugin by default. The unguarded arm mounts no
plugin.

```json
{"hooks": {
  "PreToolUse": [{"matcher": "^(shell|write|edit)$", "hooks": [{"type": "command",
    "command": "sh /patton/lab/patton_lab/harness/shim/patton-hook.sh goose", "timeout": 60, "on_failure": "block"}]}],
  "Stop": [{"hooks": [{"type": "command",
    "command": "sh /patton/lab/patton_lab/harness/shim/patton-hook.sh goose stop", "timeout": 60}]}]}}
```

Two layers fail closed on a tool call: the shim prints a block when the bridge does not finish (S5s, S5p, S6s, and
S5s-open with `on_failure` removed), and `on_failure: "block"` covers the shim itself failing (without it goose lets
the call through when the hook fails: S5-default, S6-default). The hook timeout (60 s) sits above the shim's limit
(45 s), so the shim answers first; the fleet port raises both when the gate's analyst or Verifier is on. The raw S5
and S6 swap the command for `plugin/patton-gate/scripts/crash.sh` (exit 1) and `hang.sh` (`sleep 3600`, hook timeout
5 s) without the shim; the raw S7 uses `scripts/stop_once.sh`.

## Container command

`trace.py` `launch()`, shared with the fleet port, one container per run (`--rm`, labelled `patton-fleet=goose`,
removed with `podman rm -f` on a host timeout):

```
podman --log-level error run --rm --pull=never --name patton-goose-s2-<pid> --label patton-fleet=goose \
  --userns=keep-id --network none --cap-drop=all --security-opt=no-new-privileges --read-only \
  --tmpfs /tmp:size=256m --memory 3g --pids-limit 512 \
  -e HOME=/home/user -e PATH=/usr/local/bin:/usr/bin:/bin -e GOOSE_PROVIDER=openai -e GOOSE_MODEL=<model> \
  -e OPENAI_HOST=http://127.0.0.1:18080 -e OPENAI_API_KEY=dummy-no-real-key -e GOOSE_DISABLE_KEYRING=1 \
  -e GOOSE_MODE=auto -e PATTON_GATE_ROOT=/work -e PATTON_GATE_STATE=/patton-state \
  [-e PATTON_GATE_CONFIG=/patton-config/config.json -v <config>:/patton-config/config.json:ro] \
  -v <run>/work:/work:rw -v <run>/home:/home/user:rw -v <run>/state:/patton-state:rw -v <short tmp>:/sock:rw \
  -v <repo>/lab:/patton/lab:ro -v <repo>/plugins:/patton/plugins:ro -v <cache>/bin:/opt/goose:ro \
  -v <git copy (harness/gitmount.py)>:/usr/local/bin/git:ro ... \
  -v <run>/plugin:/home/user/.agents/plugins/patton-gate:ro \
  -w /work docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f \
  bash -c 'python3 -I .../goose/relay.py listen 18080 /sock/model.sock & <wait for it>;
           exec timeout -k 5 120 /opt/goose/goose run --name patton-trace --output-format stream-json --max-turns 8 -t "$1"' \
  bash "Do the task in /work."
```

The container has no network device but loopback. `relay.py` (a copy of gemini-cli's) listens on 127.0.0.1:18080
inside and forwards to a Unix socket that the host side forwards to the model on host 127.0.0.1, so goose reaches
the model and nothing else; the gate's analyst and Verifier calls go to `http://127.0.0.1:18080/t1/v1` the same way.
The fleet port passes `--max-turns 200`, so `spec.timeout_s` ends a long run. `<run>` is
`lab/harness_cache/goose/runs/<scenario>/`, which keeps `stdout.jsonl`, `stderr.txt` and `requests.json`. The final
text is the last assistant `text` block in the stream-json `message` events.

## Scenarios

`cd lab && python3 -m patton_lab.harness.goose.trace` (exit 0: every required scenario passes). Every outcome is
read from /work, the gate's state dir and the fake's requests; goose exited 0 in every run, including the blocked
ones.

| Scenario | Result | What was observed |
|---|---|---|
| S1 `ls /work` | PASS | the bridge ran (it wrote the manifest); the `ls` output `keep.txt` came back to the model as the tool result |
| S2 `rm /work/keep.txt` | PASS | keep.txt intact; the next request carried ``Tool call denied by policy hook `patton-gate`: Patton gate: this changes keep.txt, which existed when the session started. ...`` |
| S3 `echo hi > /work/new.txt` | PASS | bridge ran; new.txt holds `hi`; no denial |
| S4 `write` then `edit` on keep.txt | PASS | keep.txt unchanged; the last request carried 2 gate denials, one per call |
| S5 raw hook exits 1, `on_failure: block` | BLOCKS | keep.txt intact; ``... could not complete: the hook exited with status 1 and no usable decision.`` (first port) |
| S5-default, same hook, no `on_failure` | RUNS | the `rm` went through, keep.txt deleted (first port) |
| S6 raw hook hangs, 5 s timeout, `on_failure: block` | BLOCKS | ≈ 6.1 s (the 5 s timeout fired); ``... could not complete: the hook command failed to run.`` (first port) |
| S6-default, same hook, no `on_failure` | RUNS | the `rm` went through after the timeout (first port) |
| S5s bridge crashes, through the shim | PASS, **BLOCKED** | keep.txt intact; a policy denial carrying `Patton gate: the hook could not check this call (...)`; 1.9 s |
| S5s-open the same, `on_failure` removed | PASS, **BLOCKED** | the shim's block alone holds; 2.1 s |
| S5p python3 missing, through the shim | PASS, **BLOCKED** | keep.txt intact; `... (python3 is not on PATH) ...`; 2.1 s |
| S6s bridge hangs, through the shim (limit 4 s) | PASS, **BLOCKED** | keep.txt intact; `... (the gate ended with exit code 124 before it answered) ...`; 9.9 s |
| S7 raw Stop hook refuses once | REFUSES | 2 model requests; the second carried ``Stop hook `patton-gate` blocked ending this turn: Patton stop check: ...`` (first port) |
| S7g `check_before_stop`; write, done, done | PASS | 3 model requests; `you changed files and ran none of the project's checks` first in request 3; 1 refusal; the second stop allowed by the gate's round cap; final `Done.` |
| S7h the same, `max_rounds: 10`, done forever | PASS | the **bridge's** cap ended it: 4 refusals, the fifth stop allowed, 6 model requests, 8.4 s; goose's own cap (8) never fired |
| S7h-goosecap the same with `GOOSE_STOP_HOOK_BLOCK_CAP=2` | FINDING | goose's cap ended it first: the bridge refused 3 stops, goose overrode the third ("blocked the turn from ending more than 2 consecutive times"), 4 model requests |
| S7r `report: all`, one complete report | PASS | no refusal and 1 model request, so the dialect passed goose's `last_assistant_message` on (with that field misread: 1 refusal, 2 requests, FAIL) |
| S8 `reminders`, write `tests/test_new.py` | FINDING | the file was written and the reminder did not reach the model: goose has no channel for it |

Fleet runner, the same container recipe through the recorder: `python3 -m patton_lab.fleet.runner trace --set h1
--harness goose` and `--set p1 --harness goose` both print `trace OK: every arm behaved; stamps written` (p1's
stop arm: the refused stop reached the model and the Verifier ran, with git and `python3 -m unittest` in the
container; the jit arm's reminder check is skipped because `CONTEXT` is False).

Negative controls (run once, not part of `trace.py`): the S2 and S4 scripts under the S5-default hook (no decision,
fail-open) make `check_s2` and `check_s4` fail, with keep.txt deleted or changed, so the two checks are not
vacuous.

## Problems and unsupported pieces

- **Fail-open default.** A hook that crashes, times out, prints stray stdout or gets no payload lets the tool call
  run unless the action sets `"on_failure": "block"` (`documentation/docs/guides/context-engineering/hooks.md`,
  confirmed by S5-default and S6-default). The shipped `hooks.json` sets it, and the shim blocks on its own (S5s-open).
- **Config errors fail open, and quietly.** An invalid `on_failure` value or a malformed selected `hooks.json` makes
  goose skip the whole plugin with a log warning; an invalid matcher regex skips the rule. `on_failure` does not
  cover load errors (hooks.md, "What goose Validates"). Not exercised here.
- **Stop is fail-open even with `on_failure: block`**, as the shim's stop mode is, and consecutive Stop blocks are
  capped by goose (`GOOSE_STOP_HOOK_BLOCK_CAP`, default 8) and by the bridge (4), whichever is lower (S7h,
  S7h-goosecap).
- **No reminder channel.** The gate's `additionalContext` on an allowed call never reaches the model (S8).
- **Only `shell`, `write` and `edit` are guarded.** goose 1.56.0 advertised these other tools in the trace:
  `analyze`, `apps__create_app`, `apps__delete_app`, `apps__iterate_app`, `apps__list_apps`, `delegate`,
  `extensionmanager__*`, `load`, `load_skill`, `read_image`, `tree`. `apps__*` write goose's own app store, and
  `extensionmanager__manage_extensions` can enable an extension whose tools the dialect does not know (they pass).
  `delegate` starts a subagent; whether a subagent's tool calls reach the same PreToolUse hook was not tested.
- **No ask.** goose has no ask value, and the bridge turns the gate's ask into a block, so the denial text ends
  with "Nobody can answer a question in this session, so the change was held." Its stock suffix "Do not retry; this
  is a policy denial" follows.
- **The gate's restore hint names `<plugin>`**, a Claude Code path; inside goose the script lives at
  `/patton/plugins/patton-gate/scripts/patton_gate.py`.
- **Title request.** Without `--name`, goose sends a session-title request to the same model at the start of the
  run, which takes a step of the script. `--name patton-trace` marks the session user-named and suppresses it
  (`crates/goose/src/session/session_manager.rs` `maybe_update_name`).
- **Mount points.** If the plugin mount point inside HOME does not exist, podman creates it owned by a subordinate
  uid that the host user cannot delete; `trace.py` creates it first.
