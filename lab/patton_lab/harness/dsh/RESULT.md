# DeepSeek Harness (dsh): patton gate port, $0 trace

Run on 2026-10-24 and again on 2026-10-25 (shim, stop and reminder support, fleet port) against a scripted model
(`patton_lab/harness/fake_chat.py`); no real model, no API key.

## Version and download

- dsh **0.2.1-rc.3** (git tag `dsh-v0.2.1-rc.3`, commit `1f81c047fc48fa190a683e469daa699857e9afe8`; npm dist-tag
  `next`). `dsh --version` inside the container prints `0.2.1-rc.3`. dsh publishes no binary release, only npm.
- `@deepseek-ai/dsh@0.2.1-rc.3`: https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-rc.3.tgz,
  sha256 `9a8f9493bfab8218215a8ca38ab8821e76dff63dc4fcb65841ef87c0aa277141`, npm integrity
  `sha512-H+E9HLnHf7fJqzen84Z9Bq8ggb5xbrcbyhZjXTJLE9NabHr3zteGvTRvdvGkxPl4ov80is4WLglFmSo+/olJjg==`. I downloaded
  the tarball once and checked its sha1 and sha512 against `npm view ... dist` before installing.
- `@deepseek-ai/dsh-hooks-claude-code@0.2.1-rc.3`: https://registry.npmjs.org/@deepseek-ai/dsh-hooks-claude-code/-/dsh-hooks-claude-code-0.2.1-rc.3.tgz,
  integrity `sha512-rAAhp0Ke1UsCUx27gFEQQneZBx8vHHA7+3XeWWkxiJtwy89Px7nX6uOE63yCFyJmfcxU7bO/6FV4BuJb+jtXrg==`.
- npm checks each installed package against its `package-lock.json` integrity hash (the lock lists 686 entries,
  optional platform packages included). The lockfile and
  manifest are kept in `install/` (lock sha256 `da1cc2f786c2a379084754acf0a561d1fef679f14ec94f7693f745a577091b3a`).
  npm 11 skipped all install scripts (koffi, node-pty, protobufjs, `dsh-subprocess-local` postinstall); none was
  needed for these runs.
- Node **v24.21.0**: https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz, sha256
  `fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6`, checked with `sha256sum -c` against the
  release's `SHASUMS256.txt` (fetched over HTTPS; its GPG signature was not checked). dsh needs Node
  `^22.19.0 || >=24.0.0`.

Install, run once (everything inside podman; nothing installed on the host):

```
C=lab/harness_cache/dsh
mkdir -p $C/dl $C/node $C/pkg $C/npm-home
curl -fsSLO --output-dir $C/dl https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz
curl -fsSLO --output-dir $C/dl https://nodejs.org/dist/v24.21.0/SHASUMS256.txt
(cd $C/dl && grep ' node-v24.21.0-linux-x64.tar.xz$' SHASUMS256.txt | sha256sum -c)
tar -xJf $C/dl/node-v24.21.0-linux-x64.tar.xz -C $C/node --strip-components=1
cp lab/patton_lab/harness/dsh/install/package.json lab/patton_lab/harness/dsh/install/package-lock.json $C/pkg/
podman run --rm --pull=never --userns=keep-id -e HOME=/npmhome -e PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin \
  -e NODE_OPTIONS=--network-family-autoselection-attempt-timeout=2000 \
  -v $C/node:/opt/node:ro -v $C/pkg:/pkg:rw -v $C/npm-home:/npmhome:rw -w /pkg \
  docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f npm ci --no-audit --no-fund
```

(I ran `npm install --save-exact` for the two packages. `npm ci` from the kept lockfile, run once into a scratch
directory, added 616 packages and installed both at 0.2.1-rc.3.
`trace.py` `check_install()` refuses to run unless both packages report 0.2.1-rc.3.)

## Dialect

`patton_lab/harness/dialects/dsh.py`. The `claude` dialect does **not** work as is: dsh's Claude-Code bridge emits
the Claude output dialect and Claude input keys, but `tool_name` is dsh's own lowercase name (`bash`, `write`,
`edit`), which `claude.parse` treats as unguarded and allows. A run with `bridge.py claude` let `rm /work/keep.txt`
through (negative control below). The `dsh` dialect maps the three names to `Bash`/`Write`/`Edit`, takes `bash`'s
`workdir` argument as the cwd for that command, and reuses `claude.emit`, `claude.emit_error` and
`claude.emit_stop`.

- **Stop.** dsh's Stop payload is `{session_id, transcript_path: "", hook_event_name, stop_hook_active: false, cwd}`
  (`hooks-claude-code/src/index.ts` `stopPayload`): no last assistant message, and the transcript path is always
  empty. The only record is dsh's own session log, `~/.dsh/sessions/<cwd key>/<session_id>/session.v4.jsonl.zstd`,
  one Zstandard frame per flush. The image's Python 3.12 has no zstd and the image no `zstd` tool, and Node's
  `zstdDecompressSync` decodes only the first frame, so `read_session.mjs` walks the frames by their headers and
  decodes each on dsh's own Node; the dialect takes the last `assistant/message` text. A failure there gives an empty
  message, and the stop is still checked (S7r shows the message is read: with the reader broken, it fails).
  `{"decision": "block", "reason"}` steers the agent with the reason as a user message.
- **Reminders.** dsh drops `additionalContext` from a PreToolUse answer (`tools/pre-execute` reads only the
  decision) but injects it from a PostToolUse answer. So `emit_context` puts a refused call's reminder in its reason,
  and leaves an allowed call's reminder in the gate's state dir under the call's id (`dsh-context/`), which the
  PostToolUse hook `context_hook.py` prints and deletes. A failure there loses the reminder and nothing else.

Unit tests: `python3 -m unittest patton_lab.harness.dsh.test_dialect` (6 pass) and `tests/test_port_dsh.py` (14
pass: stop parse, cap, emit_stop, the session reader on a multi-frame file with a torn tail, the reminder carrier,
the fleet port with its launch stubbed).

## Hook config

Four pieces, all under `patton_lab/harness/dsh/`:

1. `patch.yml`, passed with `--patch`: a hand-declared `openai-completions` route `lab` to the model (`baseURL` and
   model name filled in per run, key from the dummy env var `LAB_API_KEY`, zero retries), `agent-default-model` set
   to it, `session-title-llm` disabled (it would take a step of the script). For a guarded run `patch-hooks.yml` is
   appended: an `insert` that mounts `@deepseek-ai/dsh-hooks-claude-code` with
   `configPath: /patton-cfg/hooks/hooks.json`, `projectDir: /work`. The unguarded arm mounts no hook plugin at all.
2. `profile/package.json`, copied to `~/.dsh/profiles/headless/package.json`, plus a link
   `~/.dsh/profiles/headless/node_modules/@deepseek-ai/dsh-hooks-claude-code -> /opt/dsh/node_modules/@deepseek-ai/dsh-hooks-claude-code`.
   The published `@deepseek-ai/dsh` lists the hook bridge only as a devDependency, so the loader cannot see it from
   the installation; it looks for profile-scope plugins in the profile's own `node_modules`
   (`packages/boot/app-boot/src/profile.ts` `installedProfilePackageNames`). Without the link dsh prints
   `dsh: warning: 1 entry did not activate ... Cannot find package '@deepseek-ai/dsh-hooks-claude-code'` and runs with
   no hooks. `trace.py` flags that case.
3. `hooks.json` (Claude Code format), every gate call through the fail-closed shim:

```json
{"hooks": {
  "PreToolUse": [{"matcher": "bash|write|edit", "hooks": [{"type": "command",
    "command": "sh /patton/lab/patton_lab/harness/shim/patton-hook.sh dsh", "timeout": 60}]}],
  "PostToolUse": [{"matcher": "bash|write|edit", "hooks": [{"type": "command",
    "command": "python3 -I /patton/lab/patton_lab/harness/dsh/context_hook.py", "timeout": 10}]}],
  "Stop": [{"hooks": [{"type": "command",
    "command": "sh /patton/lab/patton_lab/harness/shim/patton-hook.sh dsh stop", "timeout": 60}]}]}}
```

   The hook timeout (60 s) sits above the shim's limit (PATTON_HOOK_LIMIT, 45 s), so the shim answers before dsh's
   timeout fires. The fleet port raises both when the gate's analyst or Verifier is on (45 s plus their timeouts).
4. `context_hook.py` and `read_session.mjs`, above.

The raw S5 and S6 swap the PreToolUse command for `scripts/crash.sh` (exit 1) and `scripts/hang.sh` (`sleep 3600`,
`timeout: 5`) with no shim, and the raw S7 uses `scripts/stop_once.sh`; S5s, S5p and S6s keep the shipped hook and
point the shim at `scripts/bridge_crash.py`, at a missing python3, or at `scripts/bridge_hang.py` with a 4 s limit.

`DSH_PERMISSION_MODE=danger-full-access` turns off dsh's own file sandbox and approval prompts, so the gate alone
decides. Under the default `workspace-write` + `ask`, a headless run has no approver and denies whatever asks.

## Container command

`trace.py` `launch()`, shared with the fleet port, one container per run (`--rm`, labelled `patton-fleet=dsh`,
removed with `podman rm -f` on a host timeout):

```
podman --log-level error run --rm --pull=never --name patton-dsh-s2-<pid> --label patton-fleet=dsh \
  --userns=keep-id --network none --cap-drop=all --security-opt=no-new-privileges --read-only \
  --tmpfs /tmp:size=256m --memory 3g --pids-limit 512 \
  -e HOME=/home/user -e PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin -e LAB_API_KEY=dummy-no-real-key \
  -e DSH_PERMISSION_MODE=danger-full-access -e PATTON_GATE_ROOT=/work -e PATTON_GATE_STATE=/patton-state \
  [-e PATTON_GATE_CONFIG=/patton-config/config.json -v <config>:/patton-config/config.json:ro] \
  -v <run>/work:/work:rw -v <run>/home:/home/user:rw -v <run>/state:/patton-state:rw -v <run>/cfg:/patton-cfg:ro \
  -v <short tmp>:/sock:rw -v <repo>/lab:/patton/lab:ro -v <repo>/plugins:/patton/plugins:ro \
  -v <cache>/node:/opt/node:ro -v <cache>/pkg:/opt/dsh:ro -v <git copy (harness/gitmount.py)>:/usr/local/bin/git:ro ... \
  -w /work docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f \
  bash -c 'python3 -I .../dsh/relay.py listen 18080 /sock/model.sock & <wait for it>;
           exec timeout -k 5 150 /opt/dsh/node_modules/.bin/dsh --profile headless --patch /patton-cfg/patch.yml --json "$1"' \
  bash "Do the task in /work."
```

The container has no network device but loopback. `relay.py` (a copy of gemini-cli's) listens on 127.0.0.1:18080
inside and forwards to a Unix socket that the host side forwards to the model on host 127.0.0.1, so dsh reaches the
model and nothing else; the gate's analyst and Verifier calls go to `http://127.0.0.1:18080/t1/v1` the same way.
dsh sends `stream: true` chat-completions requests. git comes from `harness/gitmount.py` so the gate can run checks
at a stop. `<run>` is `lab/harness_cache/dsh/runs/<scenario>/`, which keeps `stdout.jsonl` (dsh's `--json` events),
`stderr.txt` and `requests.json`. The final text is the closing `{"type": "final", "text"}` event.

## Scenarios

`cd lab && python3 -m patton_lab.harness.dsh.trace` (exit 0: every required scenario passes). Every outcome is read
from /work, the gate's state dir and the fake's requests; dsh exited 0 in every run, including the blocked ones.

| Scenario | Result | What was observed |
|---|---|---|
| S1 `ls /work` | PASS | hook plugin loaded; the bridge ran (it wrote the manifest); the tool result `keep.txt` reached the model |
| S2 `rm /work/keep.txt` | PASS | keep.txt intact; the tool result the model saw was `Error: Patton gate: this changes keep.txt, which existed when the session started. ...` |
| S3 `echo hi > /work/new.txt` | PASS | bridge ran; new.txt holds `hi`; no denial |
| S4 `read`, then `write` and `edit` on keep.txt | PASS | keep.txt unchanged; 2 of 2 write/edit results were gate denials |
| S5 raw hook exits 1 | RUNS | the `rm` went through, keep.txt deleted; tool result `(no output)` (kept from the first port) |
| S6 raw hook hangs, `timeout: 5` | RUNS | the timeout fired, then the `rm` ran (kept from the first port) |
| S5s bridge crashes, through the shim | PASS, **BLOCKED** | keep.txt intact; `Error: bridge_crash.py: simulated crash` then `Patton gate: the hook could not check this call (the gate ended with exit code 1 before it answered), so it blocks it.`; 3.8 s |
| S5p python3 missing, through the shim | PASS, **BLOCKED** | keep.txt intact; `... could not check this call (python3 is not on PATH) ...`; 4.0 s |
| S6s bridge hangs, through the shim (limit 4 s) | PASS, **BLOCKED** | keep.txt intact; `... (the gate ended with exit code 124 before it answered) ...`; 13.4 s |
| S7 raw Stop hook refuses once | REFUSES | 2 model requests; the second ended with `Patton stop check: ...` (kept from the first port) |
| S7g `check_before_stop`; write, done, done | PASS | 3 model requests; the gate's `you changed files and ran none of the project's checks` first in request 3; 1 refusal; the second stop allowed by the gate's round cap; final `Done.` |
| S7h the same, `max_rounds: 10`, done forever | PASS | the bridge refused 4 stops and allowed the fifth: 6 model requests, 10.1 s. dsh has no cap of its own |
| S7r `report: all`, one complete report | PASS | no refusal and 1 model request, so the dialect read the final message from the session log (with the reader broken: 1 refusal, 2 requests, FAIL) |
| S8 `reminders`, write `tests/test_new.py` | PASS | the file was written and `Patton: tests/test_new.py is a test. ...` reached the next request as a user message, through the PostToolUse carrier |

Fleet runner, the same container recipe through the recorder: `python3 -m patton_lab.fleet.runner trace --set h1
--harness dsh` and `--set p1 --harness dsh` both print `trace OK: every arm behaved; stamps written` (p1's stop arm:
the refused stop reached the model and the Verifier ran, with git and `python3 -m unittest` in the container).

Negative controls (run once, not part of `trace.py`): the S2 script under the S5 crashing hook, and under
`bridge.py claude`, both left keep.txt deleted and failed `check_s2`; the S4 script under the crashing hook
overwrote keep.txt and failed `check_s4`. S4 reads keep.txt first because dsh's own read-before-write policy
otherwise refuses the write and edit (`Error: cannot modify "/work/keep.txt": file has not been read`), and the
file would stay unchanged without the gate.

## Problems and unsupported pieces

- **Every hook failure fails open in dsh itself, and there is no switch to fail closed.** A crash (any exit other
  than 0 and 2), a timeout, or a spawn failure is "a non-blocking error" (`packages/experimental/hook-protocol/src/runner.ts`,
  `codec.ts`); the tool runs (S5, S6). The shim closes this for everything after `sh` starts (S5s, S5p, S6s). What
  remains open: `sh` itself missing, the shim killed by dsh's own timeout before its limit (the shipped timeouts
  keep the shim's limit 15 s under dsh's), and a hook plugin or `hooks.json` that does not load.
- **Plugin and config failures fail open.** A hook plugin that does not import (seen before the profile link was
  added) prints one stderr warning and the agent runs with no hooks; per the README, an unreadable or unparsable
  `hooks.json` does the same. `trace.py` fails S1 to S4 when the warning appears or no manifest was written.
- **The hook bridge is not in the published CLI's dependencies**, so it needs the profile link described above, an
  extra `npm install` of the same version, or `dsh plugin --profile headless add` (pnpm, network).
- **Stop has no loop cap and no `stop_hook_active`** (always false); the bridge's PATTON_BRIDGE_STOP_CAP (4) is the
  only cap (S7h). The Stop payload has no final message; the dialect reads it from dsh's session log, a format
  dsh may change.
- **Reminders need the PostToolUse carrier.** dsh ignores `additionalContext` at PreToolUse; the reminder arrives
  after the call ran, as a user message before the next request.
- **Only `bash`, `write` and `edit` are guarded.** The headless profile also advertised `workflow` (runs a JavaScript
  orchestration script), `subagent`, `subagent_fork`, `send_message`, `job_kill`, `todo_write`, goal and plan tools,
  `web_fetch`, `web_search`, `skill`, `working_directory`, `read`, `read_image`, `glob`, `grep`. Whether a subagent's
  `bash`/`write`/`edit` calls reach the same `tools/pre-execute` hook was not tested.
- **`updatedInput`, `systemMessage` and `{"continue": false}` are ignored** by dsh's bridge (README, Known
  Limitations), so the gate's stop-time `systemMessage` (what was left open at the round cap) is not shown.
- **No ask.** In headless the bridge turns the gate's ask into a block, so the denial ends with "Nobody can answer a
  question in this session, so the change was held." The restore hint names `<plugin>`, a Claude Code path; here the
  script is at `/patton/plugins/patton-gate/scripts/patton_gate.py`.
- **Developer preview.** Every dsh release is a prerelease; the README warns of breaking changes and `SAFETY.md`
  says not to use it as a sole control.
