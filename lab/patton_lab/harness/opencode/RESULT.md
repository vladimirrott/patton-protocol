# OpenCode port of the Patton gate: $0 trace result

Date: 2026-10-24. Run: `cd lab && python3 -m patton_lab.harness.opencode.trace` (add `--keep` to keep each run's
directory under the system temp dir). Exit 0: S1 to S6 all passed.

## Exact build

| What | Value |
|---|---|
| Release | OpenCode v1.19.4 (tag `v1.19.4`, source SHA `148842ef7cb1bc34192b3fff57d0f9721ab9c801`) |
| Wrapper package | `opencode-ai@1.19.4`, https://registry.npmjs.org/opencode-ai/-/opencode-ai-1.19.4.tgz, sha256 `2ceca45c7e0bbd50ddd3f4d6d651ed43645647d74279d656e7c7747a4b6a9137`, npm integrity `sha512-O3rTc9OCd6CeeJohCAjFYD3MX5U0weKzKnySfsJ9sw9uAbNZZK4meIcjd+7k3inmZB5G2qONSen3yA45yECiFA==` |
| Platform package (what runs) | `opencode-linux-x64@1.19.4`, https://registry.npmjs.org/opencode-linux-x64/-/opencode-linux-x64-1.19.4.tgz, sha256 `dca7b06bbb4e4a1ab3681b799cd328057b356d9399e8e47336cf53b7ae658d0d`, npm integrity `sha512-rQ/l3jmJoxf8ZYnBargg9alURlz6buEilysGODtzkcmcYtcZ91LOgF034oUUHHQl8/y1hbRTDvg6ujqUjQsMXw==` (matches `npm view`) |
| Binary | `package/bin/opencode`, sha256 `538fcb1b6408c8d4608822acee19f7ff5b3a3eb3383c0633c4f06170857312a7`; `opencode --version` in the container prints `1.19.4` |
| Published checksum | GitHub release asset https://github.com/anomalyco/opencode/releases/download/v1.19.4/opencode-linux-x64.tar.gz has the published digest `sha256:849bd6b3d959a1d2ef200ed1634a51c79a45ca236aa26665d75debd694805cce`; the downloaded file matches it, and the `opencode` binary inside is byte-identical to the npm one (same sha256 as above) |

Fetched with `npm pack --ignore-scripts` into `lab/harness_cache/opencode/dl/` and unpacked into `pkg/` (both
gitignored). Nothing was installed on the host; the binary only ever ran in podman. `trace.py` refuses to run when
the binary's sha256 differs from the pin.

## Files

- `plugin/patton-gate.js`: the plugin. `tool.execute.before` pipes `{tool, args, sessionID, callID, directory}` to
  `python3 lab/patton_lab/harness/bridge.py opencode` with `spawnSync` (timeout `PATTON_BRIDGE_TIMEOUT_MS`, default
  30 000 ms, SIGKILL) and throws unless the bridge exited 0 with `{"allow": true}`. A spawn error, a timeout, a
  signal, a non-zero exit and unreadable stdout each throw a reason that names what failed.
- `../dialects/opencode.py`: `bash` becomes Bash (with `workdir` as cwd), `edit`/`write` become Edit/Write on
  `filePath`, `apply_patch` becomes `rm -f -- <every path in the patch>` (an unreadable patch raises, so the bridge
  blocks). Read, search, todo, skill, question and `task` pass. Any other tool goes to the gate under its own name
  and is refused.
- `podrun.py`, `relay.py`: the container runner and its only network path (shared with the Cline trace).
- `trace.py`: the scenarios.
- `../cline/test_dialect.py`: host-side unit tests of both dialects through the real bridge and gate.

## Endpoint configuration

All of it comes from `OPENCODE_CONFIG_CONTENT`:

```json
{"provider": {"lab": {"npm": "@ai-sdk/openai-compatible", "name": "Lab scripted model",
   "options": {"baseURL": "http://127.0.0.1:18080/v1", "apiKey": "dummy"},
   "models": {"scripted": {"name": "scripted", "tool_call": true}}}},
 "model": "lab/scripted",
 "plugin": ["file:///repo/lab/patton_lab/harness/opencode/plugin/patton-gate.js"],
 "permission": "allow", "autoupdate": false, "share": "disabled", "lsp": false, "formatter": false}
```

plus `OPENCODE_DISABLE_AUTOUPDATE=1` and `OPENCODE_DISABLE_MODELS_FETCH=1`. `"permission": "allow"` means the plugin
is the only thing that can refuse a call. The model id `scripted` holds no `gpt-`, so OpenCode offers `edit` and
`write` rather than `apply_patch` (`tool/registry.ts`).

## Container

```
podman --log-level error run --rm --pull=never --userns=keep-id --network=none \
  --cap-drop=all --security-opt=no-new-privileges --read-only --tmpfs /tmp:size=256m \
  --memory=3g --pids-limit=1024 \
  -e HOME=/home/user -e PATTON_GATE_ROOT=/work -e PATTON_GATE_STATE=/state -e PYTHONDONTWRITEBYTECODE=1 \
  -e OPENCODE_CONFIG_CONTENT=<above> -e OPENCODE_DISABLE_AUTOUPDATE=1 -e OPENCODE_DISABLE_MODELS_FETCH=1 \
  -v <run>/work:/work:rw -v <run>/home:/home/user:rw -v <run>/state:/state:rw -v <run>/relay:/relay:ro \
  -v <repo>/lab:/repo/lab:ro -v <repo>/plugins:/repo/plugins:ro \
  -v lab/harness_cache/opencode/pkg/opencode-linux-x64-1.19.4/package:/opt/opencode:ro \
  -w /work docker.io/library/python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f \
  timeout -k 5 150 python3 -I /repo/lab/patton_lab/harness/opencode/relay.py /relay/chat.sock 18080 -- \
  /opt/opencode/bin/opencode run --format json --title lab-<scenario> -m lab/scripted \
    --print-logs --log-level WARN "Do the task."
```

Network: `--network=none`, so the container has only its loopback. The host serves a unix socket that forwards to
the FakeChat port on the host's 127.0.0.1 (`relay.HostRelay`), and inside the container `relay.py` listens on
127.0.0.1:18080 and forwards to that socket. I did not use `slirp4netns:allow_host_loopback=true` as the Codex
runner does: a probe through it fetched http://example.com (HTTP 200), so it reaches the internet as well as host
loopback. Under `--network=none` the same probe fails with a name-resolution error, and 10.0.2.2 is unreachable.

`--title` matters: without it OpenCode forks a title request to the same model at step 1, and that request takes
the next scripted step (observed: the title request got the scripted `bash` call).

## Scenarios (final run, 2026-10-24)

Each is one container run with its own script. Outcomes come from /work, the fake's requests and the tool parts of
the JSON stream; the exit code is reported, never trusted.

| | Scenario | Observed | Result |
|---|---|---|---|
| S1 | `bash ls /work` | tool part `completed`, output lists keep.txt; the gate's manifest exists, so the bridge ran | PASS |
| S2 | `bash rm /work/keep.txt` | tool part `error`: "Patton gate: this changes keep.txt, which existed when the session started. ..."; keep.txt intact; the refusal text is in request 2 to the fake | PASS |
| S3 | `bash printf ... > /work/new.txt` | new.txt holds the text; bridge ran | PASS |
| S4 | `edit` keep.txt, then `write` keep.txt | both parts `error` with the gate's refusal; keep.txt unchanged; refusal reached the model | PASS |
| S5 | bridge crashes: `PATTON_BRIDGE_CMD=["python3","-c","import sys; sys.exit(1)"]`, and bridge cannot start: `["/nonexistent/bridge"]`, each on a call the gate would allow (`printf x > /work/s5.txt`) | blocked: "Patton gate bridge exited 1 with no readable answer, so it blocks this call." and "Patton gate bridge failed to start (ENOENT ... posix_spawn '/nonexistent/bridge') ..."; the file was not created in either run | PASS |
| S6 | bridge hangs: `["sleep","600"]` with `PATTON_BRIDGE_TIMEOUT_MS=3000`, on an allowed call | blocked: "Patton gate bridge timed out after 3000 ms, so it blocks this call."; file not created | PASS |
| S7 | refuse a stop | Finding: no hook can. `Hooks` in `packages/plugin/src/index.ts` has no stop or turn-end hook (the list: event, config, tool, auth, provider, chat.message, chat.params, chat.headers, permission.ask, command.execute.before, tool.execute.before, shell.env, tool.execute.after, experimental.*, tool.definition). `session.idle` reaches the `event` hook, which returns void, and `opencode run` exits when the session status turns idle (`cli/cmd/run.ts` line 786). A plugin could only send a new prompt afterwards | finding |

Proof the checks can fail: with `"plugin": []`, S2 and S4 both FAIL (the `rm`, `edit` and `write` complete and
keep.txt is changed or gone); with a bridge command that always prints `{"allow": true}`, the S5 check FAILS (the
file is created). In the early probes (under slirp4netns) a scenario took about 25 s, about 17 s of it between
OpenCode loading its config and creating the session; the whole trace of seven runs finishes in a few minutes.

## Problems and unsupported pieces

- A thrown `tool.execute.before` error reaches the model as a tool error (part `status: "error"`, the message is the
  thrown text). It surfaces through `Effect.promise`, which OpenCode treats as a defect, but the AI SDK still turns
  it into a tool-error result; observed, not just read.
- No ask: the plugin can only allow or throw. The bridge turns the gate's ask into a block with "Nobody can answer a
  question in this session, so the change was held." The `permission.ask` hook is declared but has no call site.
- No stop refusal (S7).
- `PATTON_BRIDGE_CMD` replaces the bridge command, so whoever sets OpenCode's environment can switch the gate off.
  It exists for the S5/S6 fault injection; a production install should drop it or ignore it.
- `apply_patch` is covered by the dialect and the unit test, but not by a container run: OpenCode only offers it to
  model ids containing `gpt-`.
- `task` (subagents) passes the gate on the assumption that a child session's tool calls go through the same plugin
  hooks (they share the instance's plugin list). Not exercised here.
- The plugin uses `spawnSync`, which blocks OpenCode's event loop for the bridge's run time (about 0.1 s per call
  here). Parallel tool calls are serialised by it.
- OpenCode tries a background `npm install @opencode-ai/plugin` in each config directory; with no network it fails
  and logs a warning, and the plugin does not need it (it imports only `node:` modules).
- No change was needed in the shared `bridge.py` or `fake_chat.py`.
