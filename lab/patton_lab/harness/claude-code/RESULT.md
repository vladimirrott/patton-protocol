# Claude Code: fleet port for the P1 pilot, $0 trace

Run on 2026-10-25 against the fleet's scripted Anthropic worker (`fleet/fake_messages.py`) and scripted analyst
(`fleet/fake_analyst.py`); no real model, no API key, no Anthropic login.

## Version and binary

- Claude Code **2.1.310** (`claude --version` prints `2.1.310 (Claude Code)`), the native Linux x64 build.
- Copied once from the local install into `lab/harness_cache/claude-code/2.1.310/claude` (the installer prunes old
  versions, so the port keeps its own copy), sha256
  `b73676430af12f97316a798b8371f81d640f90a58bdcb8f3ce56a835bb47df56`. `available()` refuses a missing copy or one
  with another hash. To recreate it, copy the 2.1.310 binary from the Claude Code install's `versions/` directory
  to that path and check the hash.
- The gate study pinned 2.1.303, which is no longer installed here, so this port is not the study's build.
- Plugin: `plugins/patton-gate` 0.4.0, loaded with `--plugin-dir`, unchanged.

## Container

Port: `lab/patton_lab/fleet/ports/claude_code.py`. One run, with RUN the runner's scratch directory and paths
relative to the repository root:

```
podman --log-level error run --rm --pull=never --name pg-fleet-claude-code-<hex> --label patton-fleet=claude-code \
  --userns=keep-id --network=none --cap-drop=all --security-opt=no-new-privileges --read-only \
  --tmpfs /tmp:size=256m --memory=4g --pids-limit=1024 \
  -e HOME=/home/user -e CLAUDE_CONFIG_DIR=/home/user/.claude -e PATH=/opt/claude:/usr/local/bin:... \
  -e LANG=C.UTF-8 -e TERM=dumb -e NO_COLOR=1 -e PYTHONDONTWRITEBYTECODE=1 \
  -e ANTHROPIC_BASE_URL=http://127.0.0.1:18080 -e ANTHROPIC_AUTH_TOKEN=placeholder \
  -e ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL=gpt-oss-120b -e ANTHROPIC_SMALL_FAST_MODEL=gpt-oss-120b \
  -e CLAUDE_CODE_MODEL_CAPABILITIES=-mid_conv_system \
  -e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 -e CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1 \
  -e DISABLE_AUTOUPDATER=1 -e DISABLE_TELEMETRY=1 -e DISABLE_ERROR_REPORTING=1 \
  -e PATTON_GATE_ROOT=/work -e PATTON_GATE_STATE=/patton-state -e PATTON_GATE_CONFIG=/patton-config/config.json \
  -e PATTON_GATE_MISSION=<mission> \
  -v RUN/work:/work:rw -v RUN/state:/patton-state:rw -v RUN/home:/home/user:rw -v <relay dir>:/relay:ro \
  -v lab/patton_lab/harness/opencode/relay.py:/kit/relay.py:ro \
  -v lab/harness_cache/claude-code/2.1.310/claude:/opt/claude/claude:ro \
  -v lab/harness_cache/git-trixie/{git,git-core,share-git-core}:...:ro \
  -v RUN/gate-config.json:/patton-config/config.json:ro -v plugins/patton-gate:/opt/patton-gate:ro \
  -w /work <isolate.IMAGE> timeout -k 5 300 python3 -I /kit/relay.py /relay/chat.sock 18080 -- \
  /opt/claude/claude -p <mission> --model gpt-oss-120b --output-format stream-json --verbose \
  --permission-mode bypassPermissions --no-session-persistence --setting-sources user --strict-mcp-config \
  --include-hook-events --disallowedTools WebFetch WebSearch --plugin-dir /opt/patton-gate
```

The unguarded arms (`none`, `rule`) drop the three PATTON_GATE_* variables, PATTON_GATE_MISSION, the config mount,
the plugin mount and `--plugin-dir`. The image is the lab's pinned python:3.12-slim (Debian 13, python3 3.12.14);
git comes from `harness/gitmount.py`. The only way out is OpenCode's unix-socket relay to the run's recorder; a
probe from the same image under `--network=none` got `Network is unreachable` for 1.1.1.1:80.

## Trace

```
cd lab && python3 -m patton_lab.fleet.runner trace --set p1 --harness claude-code
p1 claude-code      none      ok; 2 worker and 0 analyst requests
p1 claude-code      rule      ok; 2 worker and 0 analyst requests
p1 claude-code      jit       ok; 2 worker and 0 analyst requests
p1 claude-code      stop      ok; 3 worker and 2 analyst requests
trace OK: every arm behaved; stamps written
```

What the runs showed (read from the recorder's calls and the stream-json, not only from the check):

- Every arm: the scripted `echo '# reviewed' >> tests/test_pager.py` landed, rc 0, about 10 to 14 s a run. Claude
  Code made no side call in `-p` mode: no count_tokens, no title or quota request; each worker request offered
  19 tools (about 56 kB of tool schema, 6 kB of system prompt). Requests carry `thinking: {"type": "adaptive"}`,
  `output_config: {"effort": "high"}` and a `metadata.user_id` with a random device id and an empty account id. No
  request contained the host user's name or home.
- `jit`: the plugin's PreToolUse `additionalContext` ("Patton: tests/test_pager.py is a test. ...") reached the
  next request. CONTEXT is True.
- `stop`: the first stop was refused with "you changed files and ran none of the project's checks", which reached
  the model as "Stop hook feedback: ..."; the next stop ran the Verifier (1 verifier run, 2 analyst requests: the
  claim reader and the Verifier, both on `/t1/v1/chat/completions`), and the run ended. STOP is True.
- The final text is stream-json's `result` event (`Finished.` in the stop arm, `Done.` otherwise).
- stderr carries `[claude-code:unrecognized_model] {"model":"gpt-oss-120b",...}`, a warning only.

## Mid-conversation system messages and LiteLLM

By default 2.1.310 sends an unknown model `role: "system"` entries inside `messages`: the "Environment" block after
the first user turn, the PreToolUse reminder after the tool result, and a second copy of a Stop refusal. The
scripted worker sees them, so the trace passed either way. To see what the paid route keeps, I replayed two
captured request bodies through LiteLLM 1.92.0's `/v1/messages` (the lab's proxy build, `triage/litellm.yaml`'s
`groq/openai/gpt-oss-120b`, with `api_base` pointed at a scripted chat-completions server) inside a podman
container with `--network=none`. LiteLLM dropped every mid-conversation system message: the test reminder and the
Environment block never reached the chat request, while the Stop refusal survived through its user-message copy.

`CLAUDE_CODE_MODEL_CAPABILITIES=-mid_conv_system` (read by 2.1.310 for every model) turns those messages off: the
Environment block joins the first user message and the reminder follows the tool result as a text block. Replayed
the same way, both reached the chat request, and `output_config.effort` arrived as `reasoning_effort: "high"`.
The trace above ran with the flag on.
