# goose: patton gate port, $0 trace

Run on 2026-10-24 against a scripted model (`patton_lab/harness/fake_chat.py`); no real model, no API key.

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
Unit tests through the real bridge and gate: `python3 -m unittest patton_lab.harness.goose.test_dialect` (6 pass).

## Hook config

A goose plugin, `patton_lab/harness/goose/plugin/patton-gate/` (`plugin.json`, `hooks/hooks.json`), copied per run
and mounted read-only at `~/.agents/plugins/patton-gate` (goose's user plugin dir,
`crates/goose/src/config/paths.rs`). goose enables a newly found plugin by default.

```json
{"hooks": {"PreToolUse": [{"matcher": "^(shell|write|edit)$", "hooks": [{"type": "command",
  "command": "python3 /patton/lab/patton_lab/harness/bridge.py goose", "timeout": 30, "on_failure": "block"}]}]}}
```

`on_failure: "block"` is required: without it goose lets the call through when the hook fails (S5-default,
S6-default). S5 and S6 swap the command for `plugin/patton-gate/scripts/crash.sh` (exit 1) and `hang.sh`
(`sleep 3600`, hook timeout 5 s); S7 adds a `Stop` hook, `scripts/stop_once.sh`.

## Container command

`trace.py` `container()`, one container per scenario (`--rm`, named `patton-goose-<scenario>-<pid>`, removed with
`podman rm -f` on a host timeout):

```
podman --log-level error run --rm --pull=never --name patton-goose-s2-<pid> --userns=keep-id \
  --network slirp4netns:allow_host_loopback=true \
  --cap-drop=all --security-opt=no-new-privileges --read-only --tmpfs /tmp:size=64m \
  -e HOME=/home/user -e GOOSE_PROVIDER=openai -e GOOSE_MODEL=scripted \
  -e OPENAI_HOST=http://10.0.2.2:<port> -e OPENAI_API_KEY=dummy-no-real-key \
  -e GOOSE_DISABLE_KEYRING=1 -e GOOSE_MODE=auto -e PATTON_GATE_ROOT=/work -e PATTON_GATE_STATE=/state \
  -v <run>/work:/work:rw -v <run>/home:/home/user:rw -v <run>/state:/state:rw \
  -v <run>/plugin:/home/user/.agents/plugins/patton-gate:ro \
  -v /home/worker/Desktop/patton-protocol/lab:/patton/lab:ro -v /home/worker/Desktop/patton-protocol/plugins:/patton/plugins:ro \
  -v /home/worker/Desktop/patton-protocol/lab/harness_cache/goose/bin:/opt/goose:ro \
  -w /work docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f \
  timeout -k 5 120 /opt/goose/goose run --name patton-trace --output-format stream-json --max-turns 8 \
  -t "Do the task in /work."
```

The fake model listens on host 127.0.0.1; the container reaches it as 10.0.2.2 and reaches nothing else.
`<run>` is `lab/harness_cache/goose/runs/<scenario>/`, which keeps `stdout.jsonl`, `stderr.txt` and
`requests.json` (every request the fake received).

## Scenarios

`cd lab && python3 -m patton_lab.harness.goose.trace` (exit 0: S1 to S4 pass). Every outcome is read from /work,
the gate's state dir and the fake's requests; goose exited 0 in every run, including the blocked ones.

| Scenario | Result | What was observed |
|---|---|---|
| S1 `ls /work` | PASS | the bridge ran (it wrote `/state/manifest.json`); the `ls` output `keep.txt` came back to the model as the tool result |
| S2 `rm /work/keep.txt` | PASS | keep.txt intact; the next request carried `Tool call denied by policy hook `patton-gate`: Patton gate: this changes keep.txt, which existed when the session started. ...` |
| S3 `echo hi > /work/new.txt` | PASS | bridge ran; new.txt holds `hi`; no denial |
| S4 `write` then `edit` on keep.txt | PASS | keep.txt unchanged; the last request carried 2 gate denials, one per call |
| S5 hook exits 1, `on_failure: block` | BLOCKS | keep.txt intact; model saw `Tool call blocked because policy hook `patton-gate` could not complete: the hook exited with status 1 and no usable decision.` |
| S5-default, same hook, no `on_failure` | RUNS | the `rm` went through, keep.txt deleted |
| S6 hook hangs, 5 s timeout, `on_failure: block` | BLOCKS | run took ≈ 6.3 s to 6.8 s (the 5 s timeout fired); model saw `... could not complete: the hook command failed to run.` |
| S6-default, same hook, no `on_failure` | RUNS | the `rm` went through after the timeout |
| S7 Stop hook refuses once | REFUSES | 2 model requests instead of 1; the second carried `Stop hook `patton-gate` blocked ending this turn: Patton stop check: ...` as a user message, then the run ended when the hook allowed |

Negative controls (run once, not part of `trace.py`): the S2 and S4 scripts under the S5-default hook (no decision,
fail-open) make `check_s2` and `check_s4` fail, with keep.txt deleted or changed, so the two checks are not
vacuous.

## Problems and unsupported pieces

- **Fail-open default.** A hook that crashes, times out, prints stray stdout or gets no payload lets the tool call
  run unless the action sets `"on_failure": "block"` (`documentation/docs/guides/context-engineering/hooks.md`,
  confirmed by S5-default and S6-default). The shipped `hooks.json` sets it.
- **Config errors fail open, and quietly.** An invalid `on_failure` value or a malformed selected `hooks.json` makes
  goose skip the whole plugin with a log warning; an invalid matcher regex skips the rule. `on_failure` does not
  cover load errors (hooks.md, "What goose Validates"). Not exercised here.
- **Stop is fail-open even with `on_failure: block`**, and consecutive Stop blocks are capped
  (`GOOSE_STOP_HOOK_BLOCK_CAP`; `crates/goose/src/agents/state_machine/ops_stop_hook.rs`).
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
