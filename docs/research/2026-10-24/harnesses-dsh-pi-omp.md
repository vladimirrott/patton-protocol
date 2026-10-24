# Harness research A: dsh, Pi, oh-my-pi

Read on 2026-10-24 (stars read from the GitHub API at 2026-10-24T16:25Z). Source trees were cloned shallow at the release tag into `~/Desktop/harness-vendor/<name>` and only read, never built or run. "V" = verified (I read the doc or source line). "U" = unverified (inferred, or not exercised).

Paths below are relative to the clone root of each harness unless stated.

---

## 1. DeepSeek Harness (`dsh`)

### Identity (V)
- Official repo: https://github.com/deepseek-ai/deepseek-harness. Confirmed: it is in the `deepseek-ai` org listing (`gh api orgs/deepseek-ai/repos`), description "DeepSeek Harness: Everything is a Plugin.", homepage https://deepseek.com/harness, README.md line 5: "`dsh` is an open-source agent harness developed by DeepSeek AI". Created 2026-08-13T11:56:32Z (matches the date in the Pinggy posts).
- Stars: 260 296 (API, 2026-10-24T16:25Z). Language: TypeScript. License: MIT (`LICENSE`, "Copyright (c) 2026 DeepSeek").
- The Pinggy name "dsh" is right. DeepSeek-TUI and "deep code cli" are not this repo.
- Status: "developer preview ... THERE WILL BE COMPATIBILITY-BREAKING CHANGES" (README.md). `SAFETY.md`: not security audited, do not use as sole control.
- Releases: every GitHub release is marked prerelease; `releases/latest` returns 404. Newest tag by date: `dsh-v0.2.1-rc.3` (2026-10-23T15:26:39Z), tag type commit, SHA `1f81c047fc48fa190a683e469daa699857e9afe8` (V, API and `git rev-parse HEAD` in clone agree).
- npm: `@deepseek-ai/dsh` dist-tags: latest = 0.2.1-rc.2, next = 0.2.1-rc.3, alpha = 0.2.1-alpha.5 (V, `npm view`). No stable (non-rc) version exists.

### Install and pin (V)
- README.md: `npx @deepseek-ai/dsh web`. Pin: `npx @deepseek-ai/dsh@0.2.1-rc.3 ...` (the `bin` is `dsh`, `apps/cli/package.json`). Node `^22.19.0 || >=24.0.0` (root `package.json` engines). Whether `npx` pinned works offline-clean is U (not executed).

### Headless mode (V: `docs/cli-help.md`, `packages/bundle/headless/README.md`)
```
dsh --profile headless "task text"          # final answer on stdout, diagnostics on stderr
echo "task" | dsh --profile headless         # task from stdin ("-" also reads stdin)
dsh --profile headless --json "task"         # newline-delimited run events on stdout
dsh --profile headless --session-id <id> "continue"
```
- `dsh headless ...` is an equivalent shorthand (`apps/cli/README.md` entry-modes table).
- Exit 0 = completed turn, 1 = aborted/errored/no turn. Without `--json` stdout is only the final answer; reasoning deltas go to stderr under `dsh: reasoning:`.
- `--json` events: opens with `session`, closes with `final`; between them `status`, `text`, `thinking`, `tool_call`, `tool_result`. Strings capped at 8 KiB (except `final`), line capped at 32 KiB. It is a projection, not the log. A failed turn can still end with `final` and exit 1, so use exit code plus `turn_end` reason.
- Session log: JSONL under `$DSH_HOME/sessions` (`packages/bundle/base/cordis.patch.yml`, `session-persistence-jsonl` root `dshHomePath('sessions')`). Default `DSH_HOME` is `~/.dsh` (logs default `~/.dsh/logs/`, `apps/cli/reference/README.md`). Log is Zstandard-compressed by default (`packages/experimental/hooks-claude-code/README.md`), so a hook script cannot read it. Exact filename layout: U.
- Credentials in headless: managed store disabled; API keys come from launch env, then `<cwd>/.env`, then `$DSH_HOME/.env` (headless README, Credentials).
- `--dump-config` prints the composed tree without booting (cli-help.md), useful to check an overlay.

### Custom OpenAI-compatible endpoint (V: `docs/user/guide/providers.md`, `packages/llm/llm-pi-ai/README.md`, `packages/bundle/base/cordis.patch.yml`)
Provider routes are config rows, edited in `$DSH_HOME/profiles/headless/cordis.patch.yml` or an overlay passed with `--patch <file>` (launcher flag, cli-help.md). A patch row replaces the target row's whole `config`.
```yaml
- id: llm-pi-ai
  config:
    providers:
      litellm:
        apiKeyEnv: LITELLM_API_KEY      # name of an env var, resolved per request
        api: openai-completions         # or openai-responses / anthropic-messages
        baseURL: http://127.0.0.1:4000/v1
        models:
          - id: <model-id-sent-to-proxy>
- id: agent-default-model
  config:
    provider: litellm                   # the providers key above
    model: <model-id-sent-to-proxy>
```
- Base default model is `deepseek-official` / `deepseek-flash` (base patch lines ~90-94), so the `agent-default-model` override is required for headless.
- Protocols offered: `openai-completions` (chat completions), `openai-responses`, `anthropic-messages` (providers.md). Tool calling goes through whichever protocol the route uses (adapter is pi-ai); that it works end to end against Groq models via LiteLLM was not tested (U).
- A route naming no key still needs a placeholder credential for OpenAI-compatible routes (llm-pi-ai README limitations). `models` list replaces the catalog for that route.
- Reasoning, headers, `compat` switches: see llm-pi-ai README (V).

### Built-in tools (V: `docs/tool-catalog.md`, generated from `ctx.tools.schemas()`)
| Tool | Args |
|---|---|
| `bash` | `description` (req), `command` (req), `timeoutMs`, `workdir`, `run_in_background` (source `packages/shell/tool-bash/src/index.ts`) |
| `read` | `file_path` (req), `offset` (1-based), `limit` |
| `write` | `file_path` (req), `content` (req) |
| `edit` | `file_path`, `old_string`, `new_string` (all req), `replace_all` |
| `subagent` | `description` (req), `prompt` (req), `cwd` (`packages/subagent/tool-subagent/src/index.ts`) |
Also `glob`, `grep`, `read_image`, `str_replace_editor`, `job_*`, `send_message`, `interrupt_agent`, `list_agents`, `web_fetch`, `web_search`, `todo_write`, `plan` tools, and more. The `edit`/`write` tools are paired with a read-before-write policy plugin. Which of these the headless profile mounts by default was not enumerated (U); the base patch mounts `tool-bash`, `tool-subagent`, `tool-subagent-fork`, `tool-jobs`.

### Permission / approval (V)
- Base patch: sandbox `workspace-write` plus approval policy `ask`, unless env `DSH_PERMISSION_MODE=danger-full-access` (approval `never`). Presets: `read-only` (ask), `workspace-write` (ask), `danger-full-access` (never) (`packages/bundle/base/cordis.patch.yml`).
- No built-in answerer in headless: `ask` resolves `unavailable` and fails closed (`packages/interaction/user-approval/README.md` line ~156); `never` is "the strict headless stance". So in headless, an `ask` decision is a deny.

### Hook / extension API
Two layers (both V):

**A. Native, in-process plugin (TypeScript, Cordis)**. Pipeline in `docs/tool-execution-pipeline.md` and `docs/subsystems/tools.md`.
- Pre-tool: `ctx.on('tools/pre-execute', (exec, next) => PreToolDecision)`. `exec` has name, parsed arguments, caller agent, signal. Returns `{kind:'allow'}`, `{kind:'deny', reason, info?}`, `{kind:'cancel'}`, or `{kind:'ask', reason?, displayReason?}`; `next()` delegates to allow. Arguments cannot be rewritten. `ask` runs only after the approval service returns `allowed-once`; missing channel means deny. Registered monotonic guards run after and can still deny. Deny reason is model-visible.
- Post-tool: `tools/post-execute` returns `accept` (optional content/value/additionalContexts) or `block` (feedback). `tools/result` is an immutable observer.
- Stop: `agent/turn-stopping` (serial), payload `{agent, turn, signal}`, "a listener that objects steers (`agent.steer(...)`) and the machine re-reads its inbox: fresh steering runs another step" (`docs/subsystems/core.md` ~line 1037). So refuse-stop = call `agent.steer(userMessage)`; return value is void.
- Subagents: `subagent/start` and `subagent/end` are emit-only (`docs/subsystems/subagent.md`, ~lines 611, 666), they cannot block. To block a spawn, deny the `subagent` tool call in `tools/pre-execute`.
- In-process only (a plugin package loaded into the profile). Mount via a patch row (`insert:` with `name:`); plugin-writing guide: `docs/cookbook/adding-a-package.md` (not read in detail, U).

**B. Claude-Code-compatible external command hooks: `@deepseek-ai/dsh-hooks-claude-code`** (`packages/experimental/hooks-claude-code/README.md`, `src/index.ts`, `packages/experimental/hook-protocol/src/codec.ts`). Experimental. `apps/cli/package.json` depends on it, but it is not mounted by default; mount with:
```yaml
- name: '@deepseek-ai/dsh-hooks-claude-code'
  config:
    configPath: ./hooks.json     # hooks.json or settings file with a `hooks` key
    projectDir: .
```
- Events supported: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStart`, `SubagentStop`. 23 other Claude events ignored.
- Command hooks only (shell string), payload JSON on stdin. Pre-tool payload: `{session_id, transcript_path:'' (always empty), hook_event_name, tool_name, tool_input, tool_use_id}` (`src/index.ts` `preToolPayload`). Hook process runs in the session cwd.
- Decisions: exit code 2 blocks with stderr as reason; exit 0 with JSON `hookSpecificOutput.permissionDecision` of `deny` or `ask` (`allow` does not pre-approve) maps to deny/ask; top-level legacy `decision` accepts `approve`/`block` only; most restrictive wins deny > ask > allow. Denied tool shows the reason or `Error: blocked by PreToolUse hook`. `ask` in headless becomes a deny (no approval channel).
- `Stop` hook: blocking (exit 2 or `decision: block`) calls `steer()` with the reason (fallback text `continue: blocked by Stop hook`) and forces another step. Known gaps: `stop_hook_active` is always false and there is no consecutive-block cap, so an unconditional blocker loops forever unless the script self-limits. Payload has no `last_assistant_message`.
- `SubagentStart` context only reaches a live in-process child; `SubagentStop` is observe-only, `agent_type` constant `general-purpose`.
- `updatedInput`, `systemMessage`, `{"continue": false}` are not honored. Hooks run serially in config order; default timeout 600 000 ms.
- Config is read once at startup; a read/parse failure logs a warning and runs no hooks (fails open). Hook crash is logged and the agent continues (fails open). A compliance hook must account for both. Whether the `headless` profile accepts this plugin row without extra dependencies is U (not run).

There is also `packages/experimental/hooks-codex` (Codex-style hooks, not read) and `claude-code-mods` (in-process `tool.call` hooks, `docs/subsystems/claude-code-mods.md`; `{ deny }` supported, but argument rewrite not).

### Subagents (V)
Built in: `subagent` (spawn) and `subagent_fork` tools, `send_message`, `interrupt_agent`, `list_agents`; in-process providers `spawn` and `fork` (base patch lines ~359-395). Child results come back via `send_message`; headless waits for child work before finishing (headless README, Run flow).

---

## 2. Pi (`pi`)

### Identity (V)
- `badlogic/pi-mono` redirects: API `repos/badlogic/pi-mono` returns `full_name: earendil-works/pi`, html_url https://github.com/earendil-works/pi. Use `earendil-works/pi`.
- Package: `packages/coding-agent`, npm `@earendil-works/pi-coding-agent`, bin `pi` (`packages/coding-agent/package.json`). Old scope `@mariozechner/pi-coding-agent` stops at 0.73.1 (npm).
- Latest release `v1.4.0` (2026-10-21T22:49:48Z, not prerelease), tag type commit, SHA `4a080252f06caf63a525d7873c5d94d3c23887e0` (V, API and clone agree). Stars 117 571, MIT (`LICENSE`, Mario Zechner), TypeScript.
- Clone: `~/Desktop/harness-vendor/pi`.

### Install and pin (V)
`npm install -g --ignore-scripts @earendil-works/pi-coding-agent@1.4.0` (`docs/quickstart.md`; Node `>=22.19.0`, package.json engines). Installer `curl -fsSL https://pi.dev/install.sh | sh` also exists (pins deps); version pin flag for the installer: U. npm dist-tag `latest` = 1.4.0.

### Headless (V: `docs/cli.md`, `docs/json.md`)
```
pi --print "prompt"                         # final assistant text on stdout, then exit
pi --mode json "prompt" > events.jsonl      # JSONL events, exits after prompt finishes
git diff | pi --print "Review this"         # piped stdin prepended to prompt
```
- With redirected streams and no mode, Pi uses print mode. `--mode rpc` is a stdin/stdout JSONL command protocol (`docs/rpc.md`).
- Useful flags: `--provider <name> --model <id[:thinking]>`, `--api-key`, `--tools <list>`, `-xt/--exclude-tools`, `-nt/--no-tools`, `-ne/--no-extensions`, `-e/--extension <path>` (repeatable; explicit paths still load under `-ne`), `--session-dir`, `--session-id <id>`, `--no-session`, `--offline`, `-na/--no-approve` (ignore project-local config), `--no-context-files`, `--system-prompt`, `--append-system-prompt`.
- JSON stream (`docs/json.md`): first record `{"type":"session","version":3,"id","timestamp","cwd"}`, then `agent_start`, `turn_start`, `message_start/update/end`, `tool_execution_start/update/end` (`toolCallId`, `toolName`, `args`, `result`, `isError`, `durationMs`), `turn_end` (`message`, `toolResults`), `agent_end` (`messages`, `willRetry`), `agent_settled` (`aborted`), plus queue, compaction, retry events. Strict LF-delimited JSONL; diagnostics on stderr.
- Session file: `~/.pi/agent/sessions/` grouped by cwd (`docs/sessions.md`); override with `--session-dir` or `PI_CODING_AGENT_SESSION_DIR`; format `docs/session-format.md`. `PI_CODING_AGENT_DIR` moves the whole config dir (`docs/environment-variables.md`). Exit code semantics for failed turns: not read (U).

### Custom endpoint (V: `docs/models.md`, `docs/models-configuration.md`)
`~/.pi/agent/models.json` (location from `src/config.ts` `getAgentDir()`):
```json
{
  "providers": {
    "litellm": {
      "baseUrl": "http://127.0.0.1:4000/v1",
      "api": "openai-completions",
      "apiKey": "$LITELLM_API_KEY",
      "models": [ { "id": "<model-id>", "contextWindow": 131072, "maxTokens": 16384 } ]
    }
  }
}
```
Then `pi --provider litellm --model <model-id> --print "..."`. `apiKey` accepts a literal, `$NAME`/`${NAME}` env interpolation, or `!command`. APIs: `openai-completions` (chat completions), `openai-responses`, `anthropic-messages`, `google-generative-ai`, others. A non-built-in provider id needs `baseUrl` plus `api`. `compat` block (e.g. `supportsDeveloperRole`, `maxTokensField`, `thinkingFormat`, `supportsUsageInStreaming`) adapts proxies; defaults are auto-detected from URL. Tool calling over chat completions is the standard path (U against Groq via LiteLLM; not tested). Setting `PI_CODING_AGENT_DIR` to a scratch dir isolates models.json per run.

### Built-in tools (V: `docs/cli.md`; schemas in `src/core/tools/*.ts`)
Default enabled: `read`, `bash`, `edit`, `write`. Also `grep`, `find`, `ls`, `powershell` (Windows); opt-in `codemode`, `tool_search`.
| Tool | Args |
|---|---|
| `bash` | `command` (req), `timeout` (seconds) |
| `read` | `path` (req), `offset` (1-indexed), `limit` |
| `write` | `path`, `content` (both req) |
| `edit` | `path` (req), `edits[]` of `{oldText, newText}` (multi-replace; legacy single-edit input is normalized, `edit.ts` lines ~54-133) |
`edit` per-edit field names were read from the guidelines string (`edits[].oldText`), full `replaceEditSchema` not read (U on `newText`).

### Permission mode (V)
None. `docs/security.md`: Pi "does not ask for approval before every tool call" and has no built-in sandbox. Project trust (`-a`/`-na`) gates project-local config only, not tool calls. Approval gating is done by extensions (example `examples/extensions/permission-gate.ts`).

### Hook / extension API (V: `docs/extensions.md`, `src/core/extensions/types.ts`, `src/core/agent-session.ts`)
In-process TypeScript, loaded with jiti (no compile), default export `(pi: ExtensionAPI) => void`; `pi --extension ./gate.ts` or `-e`. Extensions run with full process permissions. Print mode binds extensions (`src/modes/print-mode.ts` line 76 `session.bindExtensions`); JSON mode goes through the same `runPrintMode` (`src/main.ts` lines 116, 987). End-to-end run of a gate in `--mode json` was not executed (U).
- **Pre-tool block**: `pi.on("tool_call", async (event, ctx) => ...)`. Event: `{type:"tool_call", toolCallId, parentToolCallId?, toolName, input}` with typed variants per tool (bash/read/edit/write/grep/find/ls/custom). Return `{block: true, reason?, terminate?}` to block; `reason` becomes the tool error text. `input` may be mutated in place (the doc: "`tool_call` can mutate input or block execution"). Return `undefined` to allow. There is no `ask` result: ask by calling `ctx.ui.confirm/select`, guarded by `ctx.hasUI` (false in print mode, so `permission-gate.ts` blocks by default there). Handlers run in load order; docs say "gate handlers should fail closed". Tool calls from one assistant message may run in parallel.
- **Post-tool**: `tool_result` (chained transformation), `tool_execution_*` notifications.
- **Refuse stop**: `agent_before_settle` (and `turn_end`). Handler receives `{entries, continue, context, outcome}`; returns `{entries?, continue?}`. `continue: true` requests one more model request, but only if the context can continue (last LLM-visible message is not an assistant message): you must also append a custom message entry (or have a queued message), otherwise it reports `agent_before_settle requested continuation without runnable model context` and does not continue (`agent-session.ts` `_buildBoundaryContext` ~line 1000, `_runBeforeSettleBoundary` ~line 2017). Docs: "Guard continuation conditions because an unconditional continuation can loop." `agent_end` is not a final-idle signal and cannot refuse; `agent_settled` is notification-only.
- **Subagent events**: none. Pi has no subagents ("skips features like sub-agents and plan mode", `README.md` line 19). `examples/extensions/subagent/` shows an optional extension that spawns separate `pi` processes; it is not built in and has no lifecycle events. Nested calls (codemode) carry `parentToolCallId`.
- Also: `input`, `before_agent_start`, `context`, `before_provider_request`, `session_start/shutdown`, `project_trust`, `registerTool`, `registerProvider`.
- Hook type: in-process only; no stdin-JSON command hooks. A bridge would be an extension that spawns a command.

---

## 3. oh-my-pi (`omp`)

### Identity (V)
- Repo https://github.com/can1357/oh-my-pi. `README.md`: "Fork of Pi by @mariozechner" (GitHub's `fork` flag is false; created 2025-12-31). MIT, TypeScript plus Rust natives (~80k lines Rust per README), stars 36 447.
- Latest release `v18.12.6` (2026-10-21T21:02:53Z, not prerelease), SHA `45c35e40c46166dc726ccc52f5d27f40f2449f99` (V, API and clone agree). npm `@oh-my-pi/pi-coding-agent` latest = 18.12.6; bin `omp` -> `src/cli.ts` (`packages/coding-agent/package.json`). Requires Bun `>=1.3.14` (package.json engines).
- Clone: `~/Desktop/harness-vendor/oh-my-pi`.

### Install and pin (V: `README.md`)
- `bun install -g @oh-my-pi/pi-coding-agent@18.12.6`, or `curl -fsSL https://omp.sh/install | sh -s -- --binary --ref v18.12.6` (prebuilt binary, SHA-256 checked). Also Homebrew, Nix, mise. Pipe-to-shell installer not executed (U).

### Headless (V: `docs/cli-reference.md`)
```
omp -p "prompt"                    # print mode: last assistant response on stdout, exit
omp -p --mode json "prompt"        # JSON event lines (-p optional with --mode json)
echo "prompt" | omp -p             # non-TTY stdin is read as prompt text
```
- Useful flags: `--model <id-or-role>`, `--thinking`, `--api-key`, `--tools a,b,c`, `--no-tools`, `--approval-mode <always-ask|always-ask-cached|write|yolo>`, `--yolo`, `-e/--extension <path>` (`--hook` is an alias), `--trusted-extension <abs-path>` (exact allowlist, disables ambient discovery; cannot combine with `-e`), `--no-extensions`, `--no-session`, `--session-dir`, `--profile <name>` (isolated auth/sessions/settings), `--config <file>` overlay, `--max-time`, `--cwd`, `--no-skills`, `--no-rules`, `--no-lsp`, `--no-title` (`PI_NO_TITLE`).
- `--mode json`: session header then JSON-line events; `message_update` omits partial snapshots; completed messages in `message_end`; terminal turn failures exit nonzero in text and JSON modes (cli-reference.md). Event vocabulary beyond that is shared with RPC (`docs/rpc.md`, not read in full, U).
- Session file: `~/.omp/agent/sessions/<encoded-cwd>/<timestamp>_<sessionId>.jsonl` (`docs/session.md` line 42); bash tool exports `PI_SESSION_ID` and `PI_SESSION_FILE` to commands (`docs/tools/bash.md`). Non-default profiles: `~/.omp/profiles/<name>/agent/`. `PI_CODING_AGENT_DIR` overrides the agent dir (`docs/models.md`).

### Custom endpoint (V: `docs/models.md`)
`~/.omp/agent/models.yml`:
```yaml
providers:
  litellm-gateway:
    baseUrl: http://127.0.0.1:4000/v1
    apiKey: LITELLM_API_KEY          # env var name (or "!command")
    api: openai-completions
    models:
      - id: <model-id>
    # optional: discovery: { type: litellm }
```
Then `omp -p --model litellm-gateway/<model-id> "..."` (fuzzy id match; exact `provider/id` form shown in cli-reference). `api` options include `openai-completions`, `openai-responses`, `anthropic-messages`. A built-in `litellm` provider exists too: base URL from config, else `LITELLM_BASE_URL`, else `http://localhost:4000/v1`, key `LITELLM_API_KEY`, with `/model_group/info` discovery (models.md ~line 480). `discovery.type: litellm` and `openai-models-list` fetch the model list, which could hit proxy `/models`; with a static `models:` list that is avoidable (U). Tool calling over chat completions: standard (U on Groq).

### Built-in tools (V: `docs/tools/*.md`)
README claims 31 built-in tools. Core four:
| Tool | Args |
|---|---|
| `bash` | `command` (req), `timeout` (seconds, default 300), `cwd`, `pty`, `async`, `name`, `ready` |
| `read` | `path` (req; selectors like `:50-100`, `:raw`) |
| `write` | `path`, `content` (req; `xd://<device>` dispatches mounted tools) |
| `edit` | default `hashline` mode: single string `input` of `[PATH#TAG]` sections with line-anchored ops, tag copied from a latest `read`/`grep`/`edit`; other modes `apply_patch`, `patch`, `replace`, `sloppy` via `edit.mode` or `PI_EDIT_VARIANT`; non-hashline fallback `replace` for some model families (DeepSeek, Kimi, GLM Flash...) |
Also `task` (subagents), `grep`, `glob`, `find`, `eval`, `browser`, `lsp`, `ast_grep`, `ast_edit`, `todo`, `web_search`, `github`, `ask`, `checkpoint`, `rewind`, etc. A hook that matches on bash `command` must also cover `eval`, which can spawn a shell (approval-mode.md says a `bash.patterns` deny does not apply to `eval`).

### Permission / approval (V: `docs/approval-mode.md`)
Tiers `read`/`write`/`exec` per tool. `tools.approvalMode`: `always-ask`, `always-ask-cached`, `write`, `yolo` (default `yolo`). `--approval-mode` overrides; `--yolo` forces yolo. `tools.approval.<tool>: allow|deny|prompt` user policy; `bash.patterns` rules (`deny` absolute); critical-pattern prompts (`rm -rf /` etc.) ignored under yolo. Approval is re-resolved after `tool_call` handlers revise input; a call that needs a prompt fails when no interactive UI exists (so non-yolo modes fail closed headless). Not a sandbox.

### Hook / extension API (V: `docs/hooks.md`, `docs/extensions.md`, `src/extensibility/shared-events.ts`, `src/session/agent-session.ts`)
In-process TypeScript, default-export factory `(pi) => {...}`; loaded from `-e`/`--hook`, `.omp/hooks/pre|post/*.ts`, `~/.omp/agent/hooks/...`, plugins. hooks.md says the legacy hook subsystem is superseded: the runtime uses `ExtensionRunner`, `--hook` is an alias of `--extension`. Print mode initializes extensions (`src/modes/print-mode.ts` line 167 `initializeExtensions`); `ctx.hasUI` is false there. No command/stdin-JSON hooks.
- **Pre-tool block**: `pi.on("tool_call", async (event, ctx) => ...)`, event `{toolName, input, ...}`; returns `{block?, reason?, input?, additionalContext?}`. `block: true` stops execution with `reason` as error; returned `input` replaces executed arguments (revalidated); a throwing handler fails closed (blocks). Runs during argument preparation, before scheduling and approval. Eval-prelude bridge calls (`browser.open`, `computer.*`) do not emit `tool_call`. Also `tool_result` (override content/isError, add context).
- **Refuse stop**: `session_stop`, "main-session stop hook, awaited before settle". Event `{messages, turn_id, last_assistant_message, session_id, session_file, stop_hook_active, signal}`. Returns `{continue?, additionalContext?}` or `{decision:"block", reason?}` (Claude/Codex-compatible). Advisory continue requests are capped at 8; explicit `decision:"block"` is not capped, "remain blocking until the hook allows completion or the operator interrupts"; a block with no reason gets a diagnostic continuation (extensions.md ~line 497; `agent-session.ts` `#emitSessionStopEvent` ~line 5453, `SESSION_STOP_CONTINUATION_CAP`). Never fires for task/subagent sessions, and defers until background jobs are idle. `agent_end` is notification-only.
- **Subagent events**: `before_subagent_spawn` (parent session, once per child; can return `{block: true, reason}` or reroute `model`). No subagent stop/end event found: I grepped `SubagentStop`, `subagent_start`, `subagent_end`, `task_end` under `src/extensibility` and found nothing. Extension factories are rebound into subagent sessions, with `ctx.agent = {kind:"main"|"sub", id, name, depth, parentId?}`, so `tool_call` and `agent_end` handlers fire inside children, but `session_stop` does not.
- Also `input`, `before_agent_start`, `context`, `before_provider_request`, `assistant_message` (rewrite text), `turn_start/end`, `session_*`, `registerTool`, `pi.exec`.

### Subagents (V)
`task` tool: batch shape `{context, tasks:[{name?, agent?, task, solutionSpace, effort?, outputSchema?, tools?, isolated?}]}` (`docs/tools/task.md`), bundled agents (`omp agents`), plan mode, goal mode, advisor.

---

## Cross-cutting notes
- All three are in-process TypeScript plugin systems at the core. Only dsh offers an external-command (stdin JSON, exit 2 or JSON decision) bridge, and it is experimental, fails open on config or hook failure, and omits several Claude fields.
- Pi cannot refuse a stop without appending a user-visible context entry. oh-my-pi's `session_stop` refuses with `decision:"block"` and no cap. dsh refuses by calling `agent.steer(...)` (native) or exit-2 `Stop` hook (no cap, no loop guard).
- None of the three was executed. Every "works against Groq through LiteLLM" statement is unverified; the config shapes come from first-party docs.
- ctx7 was not used: the repos' own docs at the release tag are the first-party source and were read directly.
- Clone dirs: `~/Desktop/harness-vendor/deepseek-harness`, `~/Desktop/harness-vendor/pi`, `~/Desktop/harness-vendor/oh-my-pi`.

---

## Summary table

| Harness | Tag | SHA | Headless cmd | Custom endpoint | Pre-tool block | Stop-block | Subagents |
|---|---|---|---|---|---|---|---|
| dsh (deepseek-ai/deepseek-harness, 260 296 stars, MIT) | `dsh-v0.2.1-rc.3` (prerelease; no stable) | `1f81c047fc48fa190a683e469daa699857e9afe8` | `dsh --profile headless [--json] "task"` (V) | Patch rows `llm-pi-ai.providers.<id>{api: openai-completions, baseURL, apiKeyEnv, models}` + `agent-default-model{provider, model}` via `--patch` or profile `cordis.patch.yml` (V; Groq untested) | Yes (V): native `tools/pre-execute` returns allow/deny/cancel/ask (ask = deny in headless); or `PreToolUse` command hook via `dsh-hooks-claude-code` (exit 2 or JSON permissionDecision deny/ask) | Yes (V): native `agent/turn-stopping` + `agent.steer()`; or `Stop` command hook exit 2 (no loop cap) | Yes (V): `subagent`, `subagent_fork`; `subagent/start`,`subagent/end` emit-only; block via pre-tool deny on the tool |
| Pi (earendil-works/pi, ex badlogic/pi-mono, 117 571 stars, MIT) | `v1.4.0` | `4a080252f06caf63a525d7873c5d94d3c23887e0` | `pi --print "prompt"` or `pi --mode json "prompt"` (V) | `~/.pi/agent/models.json` providers.<id>{baseUrl, api: openai-completions, apiKey: "$ENV", models[]} + `--provider --model` (V; Groq untested) | Yes (V): in-process `pi.on("tool_call")` returns `{block, reason}`; no ask result, use `ctx.ui` only when `ctx.hasUI` | Partial (V): `agent_before_settle` returns `{continue:true}` plus an appended user-role entry; no hard refusal, loop risk | No built-in subagents (V); example extension spawns `pi` processes, no events |
| oh-my-pi (can1357/oh-my-pi, 36 447 stars, MIT, fork of Pi) | `v18.12.6` | `45c35e40c46166dc726ccc52f5d27f40f2449f99` | `omp -p "prompt"` or `omp -p --mode json "prompt"` (V) | `~/.omp/agent/models.yml` providers.<id>{baseUrl, api: openai-completions, apiKey: ENV_NAME, models[]} + `--model <provider>/<id>`; built-in `litellm` provider reads `LITELLM_BASE_URL` (V; Groq untested) | Yes (V): in-process `pi.on("tool_call")` returns `{block, reason, input?}`; also `tools.approval.<tool>: deny` policy and approval modes | Yes (V): `session_stop` returns `{decision:"block", reason}` (uncapped) or `{continue, additionalContext}` (capped at 8); main session only | Yes (V): `task` tool; `before_subagent_spawn` can block; no subagent-stop event found; `ctx.agent.kind==="sub"` in child sessions |
