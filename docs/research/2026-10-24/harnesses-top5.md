# Harness research B: five open-source terminal coding-agent harnesses

Date: 2026-10-24. Star counts read live with `env -u GITHUB_TOKEN gh api repos/OWNER/REPO` at 2026-10-24T16:32:46Z (first pass at 16:17:35Z, within 15 stars). Vendor clones live in `~/Desktop/harness-vendor/<name>`; all code there was read only, nothing built, installed or run. One caveat on that: I ran one inline `python3 -I -c` JSON read of a hook schema inside the open-interpreter clone (isolated mode, no clone code imported), then switched to `yq`.

Legend: [V] verified from a cited file, doc or API call. [U] unverified (inferred, or needs a runtime test).

## 1. Ranking table

| Repo | Stars | Read at (UTC) | Archived | Last push | License | Decision |
|---|---|---|---|---|---|---|
| anthropics/claude-code | 153,256 | 16:32:46 | no | 2026-10-24 | none declared | Excluded (already integrated). Count recorded only. |
| openai/codex | 130,780 | 16:32:46 | no | 2026-10-23 | Apache-2.0 | Excluded (already integrated). Count recorded only. |
| anomalyco/opencode (sst/opencode redirects here) | 220,983 | 16:32:46 | no | 2026-10-24 | MIT | PICK 1 |
| google-gemini/gemini-cli | 107,786 | 16:32:46 | no | 2026-10-24 | Apache-2.0 | PICK 2, with a flag. README banner says Gemini CLI was replaced by Antigravity CLI on 2026-06-18 for unpaid-tier and Google One users. Repo is not archived, 81 commits since 2026-10-01, release v0.65.0 on 2026-10-20. Not retired for API-key use. |
| OpenHands/OpenHands (was All-Hands-AI/OpenHands) | 91,730 | 16:32:46 | no | 2026-10-24 | MIT | DROPPED. The repo is now "Agent Canvas", a web control center (README.md). Its terminal CLI, OpenHands/OpenHands-CLI (267 stars), has a README commit of 2026-08-11 reading "no longer actively maintained" (954f2ba6). The maintained engine is a Python library plus agent server (OpenHands/software-agent-sdk v1.58.1), not a terminal harness. |
| cline/cline | 70,813 | 16:32:46 | no | 2026-10-24 | Apache-2.0 | PICK 3 (CLI lives in `apps/cli`) |
| code-yeongyu/oh-my-openagent | 70,306 | 16:32:46 | no | 2026-10-24 | SUL-1.0 (GitHub: NOASSERTION) | DROPPED. LICENSE.md is the Sustainable Use License: internal-business or non-commercial use, free non-commercial redistribution only. Not open source by OSI standards. It is also a layer on other harnesses (package.json: "OpenCode Plugin"; packages omo-opencode, omo-codex, omo-senpi) and README 5.0 says "OmO loves Pi", so it overlaps OpenCode, Codex and pi. |
| openinterpreter/openinterpreter (was openinterpreter/open-interpreter) | 68,656 | 16:32:46 | no | 2026-10-11 | Apache-2.0 | PICK 4. Now a Rust fork of Codex (FORK_BRANDING.md, docs/open-interpreter-delta.md). Fewer commits (6 since 2026-10-01) but a release on 2026-10-07. |
| aaif-goose/goose (was block/goose) | 55,633 | 16:32:46 | no | 2026-10-24 | Apache-2.0 | PICK 5 |
| Aider-AI/aider | 49,735 | 16:32:46 | no | 2026-05-22 | Apache-2.0 | Rank 6. Latest release v0.86.0 on 2025-08-09, 0 commits since 2026-10-01. Stale, and it has no tool-call hook API. |
| continuedev/continue | 36,311 | 16:32:46 | no | 2026-10-24 | Apache-2.0 | Below cut. IDE-first. |
| charmbracelet/crush | 28,791 | 16:32:46 | no | 2026-10-24 | NOASSERTION | Below cut. |
| QwenLM/qwen-code | 28,696 | 16:32:46 | no | 2026-10-24 | Apache-2.0 | Below cut. |
| Kilo-Org/kilocode | 27,732 | 16:32:46 | no | 2026-10-24 | MIT | Below cut. |
| RooCodeInc/Roo-Code | 24,265 | 16:32:46 | YES | 2026-05-15 | Apache-2.0 | Dropped, archived. |
| MoonshotAI/kimi-cli | 11,422 | 16:32:46 | YES | 2026-09-22 | Apache-2.0 | Dropped, archived. |
| OpenHands/OpenHands-CLI | 267 | 16:32:46 | no | 2026-10-22 | MIT | Dropped, README says unmaintained. |

Ranking rule applied: by stars, excluding the two integrated harnesses and the three other-agent harnesses (pi, oh-my-pi, DeepSeek's harness). Picks in rank order: opencode, gemini-cli, cline, open-interpreter, goose. The 6th is aider (stale). A web search (2026-10, several roundups) surfaced no other open-source terminal agent above 55k stars besides the excluded ones. [V for counts; web roundups are secondary]

Dropped clones kept for reference only (not picks): `openhands` v1.33.0 b968abef4779eb47f5843c975a60c7b4ccf49886; `openhands-cli` 1.16.0 2963442dacc7cea44e39b7c4e73724295c853465; `openhands-sdk` v1.58.1 60192ed54db73ac658ac1a07462916126c32d3a0; `oh-my-openagent` v5.1.45 1b6b9d0d6c0d7ed06c52ffa87d19c0d1bbeafd8f. If the OpenHands SDK is wanted anyway: it has Claude-Code-style hooks (`openhands-sdk/openhands/sdk/hooks/types.py`: PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, SessionEnd, Stop; exit 2 blocks; Stop deny keeps the agent running per `manager.py` `run_stop`) [V], but it is a Python SDK, not a terminal harness.

---

## 2. OpenCode

Source: https://github.com/anomalyco/opencode (clone `~/Desktop/harness-vendor/opencode`).

**Release, license, language.** Latest GitHub release `v1.19.4` (2026-10-22) [V, `gh api releases/latest`]. SHA `148842ef7cb1bc34192b3fff57d0f9721ab9c801` [V, `git rev-parse HEAD`]. MIT (`LICENSE`), TypeScript, Bun monorepo [V]. Note: git tags `v2.1.0` to `v2.1.4` exist but have no GitHub releases; the latest release stays v1.19.4 [V].

**Install, pin.** `npm i -g opencode-ai@1.19.4` (README.md lists `npm i -g opencode-ai@latest`; `npm view opencode-ai@1.19.4 version` returned 1.19.4) [V]. Also `curl -fsSL https://opencode.ai/install | bash` and brew [V, README.md].

**Headless.** `opencode run [message..]` runs one prompt and exits (`packages/opencode/src/cli/cmd/run.ts`). Flags: `--format default|json` (raw JSON events), `-m provider/model`, `--agent`, `--dir`, `--auto` / `--dangerously-skip-permissions` / hidden `--yolo` (auto-approve), `-c/-s` session, `--attach URL` [V]. JSON mode writes one object per line: `{type, timestamp, sessionID, ...}` with types `tool_use`, `step_start`, `step_finish`, `text`, `reasoning`, `error` (run.ts lines 668-780) [V]. Without `--auto`, any permission request is auto-rejected in `run` (run.ts lines 791-808) [V].

**Custom OpenAI-compatible endpoint.** `opencode.json` (or env `OPENCODE_CONFIG_CONTENT` / `OPENCODE_CONFIG`, `packages/core/src/flag/flag.ts`) [V]:
```json
{"provider":{"litellm":{"npm":"@ai-sdk/openai-compatible","name":"LiteLLM",
 "options":{"baseURL":"http://127.0.0.1:4000/v1","apiKey":"{env:LITELLM_KEY}"},
 "models":{"<model-id>":{"name":"x"}}}}}
```
Doc: `packages/web/src/content/docs/providers.mdx` (the Atomic Chat example, lines 375-407) and `config.mdx` line 901 for `{env:VAR}` substitution [V]. Model flag then `-m litellm/<model-id>`. Tool calling is on by default for this provider; the docs warn weak tool-calling models misbehave (providers.mdx line 405) [V]. Whether a given Groq model emits valid tool calls through LiteLLM is [U].

**Built-in tools** (`packages/opencode/src/tool/*.ts`; permission/hook ids):
- `bash` (shell): `{command: string, timeout?: ms, workdir?: string}` (`tool/shell/prompt.ts` lines 15-22; id fixed to "bash" in `tool/shell/id.ts`) [V].
- `edit`: `{filePath, oldString, newString, replaceAll?}` (`tool/edit.ts` lines 47-55) [V].
- `write`: `{filePath, content}` (`tool/write.ts` lines 20-22) [V].
- others: `read`, `glob`, `grep`, `apply_patch`, `task` (subagents), `webfetch`, `websearch`, `todowrite`, `skill`, `question`, `plan` [V, file list].

**Hook/plugin API.** In-process JS/TS plugin, loaded from `.opencode/plugins/` or `~/.config/opencode/plugins/` or the `plugin` config array (`config.mdx` lines 801-812) [V]. Type `Hooks` in `packages/plugin/src/index.ts` lines 222-333 [V].
- Pre-tool: `"tool.execute.before": (input:{tool, sessionID, callID}, output:{args}) => Promise<void>`. To BLOCK, the handler throws; the documented example is `throw new Error("Do not read .env files")` (`packages/web/src/content/docs/plugins.mdx` lines 247-257) [V]. It can also mutate `output.args`. Trigger sites: `session/tools.ts` lines 107, 176, 259, 339, 403 and `session/prompt.ts` line 308 (the `task` tool) [V]. How a thrown error surfaces to the model (tool error versus defect) was not run [U]. There is no allow/deny/ask return value.
- `"permission.ask"` hook is declared in the type (line 261, output status "ask"|"deny"|"allow") but a grep of `packages/**/*.ts` finds no call site that triggers it, so treat it as dead [V by grep, runtime U].
- Stop/turn end: NO blocking hook. `event` hook receives `session.idle` and other events (plugins.mdx lines 160-190) but returns void [V]. A plugin could re-prompt through the SDK client after `session.idle` as a workaround (not a refusal of the stop) [U].
- Subagent events: no dedicated hook. Subagents are `task` tool calls (hook fires with tool "task"); child sessions carry `parentID` (`session/session.ts`), and `session.created` events can be watched [V for parentID; U for event payload].
- No external-command (stdin JSON) hook mechanism; plugins are in-process only [V by absence in docs/types].

**Subagents.** Yes: `task` tool (`tool/task.ts`: `{description, prompt, subagent_type, ...}`, background mode) [V].

**Permissions.** `permission` config: per tool `allow|ask|deny`, glob patterns, `external_directory`; `"permission":"allow"` for everything; `--auto` approves all non-denied requests and explicit deny rules still apply (`permissions.mdx` lines 14-36, 62) [V]. Env `OPENCODE_PERMISSION` (JSON) merges into config (`config/config.ts` line 559) [V].

---

## 3. Gemini CLI

Source: https://github.com/google-gemini/gemini-cli (clone `~/Desktop/harness-vendor/gemini-cli`).

**Release, license, language.** Latest release `v0.65.0` (2026-10-20); SHA `6006c14a4decfe4b891c6263ad6ad5992e404b5b` [V]. Apache-2.0, TypeScript, package `@google/gemini-cli` 0.65.0 [V]. README banner: replaced by Antigravity CLI on 2026-06-18 for unpaid-tier and Google One users; "not optimized for the latest Gemini models" [V]. Weekly nightlies continue.

**Install, pin.** `npm install -g @google/gemini-cli@0.65.0` (`npm view` confirmed the version exists; README lists the `latest|preview|nightly` forms) [V].

**Headless.** `gemini -p "prompt"` (non-interactive, forces headless) with `--output-format text|json|stream-json`, `--approval-mode default|auto_edit|yolo` or `--yolo` (`docs/cli/headless.md`, `docs/reference/configuration.md` lines 2894-2975) [V]. JSON: `{response, stats, error?}`. stream-json JSONL events: `init`, `message`, `tool_use`, `tool_result`, `error`, `result`. Exit codes 0, 1, 42 (input error), 53 (turn limit) [V]. In headless mode, a policy rule that would "ask" is treated as deny (`docs/reference/policy-engine.md` line 117) [V]; use yolo for unattended runs. Yolo also enables sandbox by default (configuration.md line 3090) [V].

**Custom endpoint (the weak point).** There is no OpenAI-compatible mode. The only base-URL knobs are `GOOGLE_GEMINI_BASE_URL` (Gemini API-key auth, must be https unless localhost) and `GOOGLE_VERTEX_BASE_URL` (`docs/reference/configuration.md` lines 2735-2749) [V]. Key via `GEMINI_API_KEY`. The wire format is Gemini `generateContent`, so the LiteLLM proxy must translate it. LiteLLM documents this (https://docs.litellm.ai/docs/tutorials/litellm_gemini_cli): set the base URL to the proxy and map Gemini model names to other providers with `model_group_alias`; a LiteLLM issue (BerriAI/litellm#17793) says the `/gemini` suffix is needed for the pass-through route and Vertex does not work [secondary sources, web search]. Google OAuth login bypasses the proxy; use API-key auth. Whether Groq open models complete tool calls end to end through that translation is [U]. Model id is whatever name the CLI sends (`-m`), aliased in LiteLLM.

**Built-in tools** (`docs/tools/file-system.md`, `docs/tools/shell.md`) [V]:
- `run_shell_command`: `{command, description?, ...}`.
- `replace` (edit): `{file_path, instruction, old_string, new_string, allow_multiple?}`.
- `write_file`: `{file_path, content}`.
- also `read_file {file_path, offset?, limit?}`, `list_directory`, `glob`, `grep_search`, MCP tools as `mcp_<server>_<tool>`.

**Hook API** (`docs/hooks/reference.md`) [V]. External command hooks only (`type: "command"`), configured in `settings.json` (`.gemini/settings.json` project, `~/.gemini/settings.json` user, `/etc/gemini-cli/settings.json` system) under `hooks.<Event>[] = {matcher, sequential?, hooks:[{type,command,name?,timeout?}]}`; default timeout 60000 ms; master switch `hooksConfig.enabled`. stdin: JSON with `session_id, transcript_path, cwd, hook_event_name, timestamp` plus event fields. stdout must be only the JSON. Exit 0 = parse stdout; exit 2 = system block with stderr as reason; any other code = warning, CLI continues (fails open).
- Pre-tool: `BeforeTool` input `tool_name, tool_input, mcp_context?, original_request_name?`. Output `{"decision":"deny"|"block","reason":"..."}` blocks and the reason goes to the model as a tool error; exit 2 does the same; `hookSpecificOutput.tool_input` rewrites arguments; `continue:false` kills the whole loop. Matcher is a regex on the tool name. There is no "ask" value (decision is allow or deny); approvals come from approval mode and the policy engine [V]. Wiring: `scheduler/scheduler.ts` line 616 `evaluateBeforeToolHook`; subagent runs use `agents/agent-scheduler.ts`, which I believe reaches the same scheduler, so inner subagent tool calls likely fire BeforeTool [U].
- Stop-block: YES. `AfterAgent` fires once per turn with `prompt, prompt_response, stop_hook_active`. `decision:"deny"` (or exit 2) rejects the response and sends `reason` (stderr) to the agent as a new prompt, forcing a retry; `continue:false` stops the session; `stop_hook_active` marks a retry round so the hook can avoid loops (hooks/reference.md lines 140-155; `core/client.ts` line 1003 fires it only when no tools are pending) [V]. Whether it fires under `-p` was not run [U].
- Other events: `BeforeAgent, BeforeModel, AfterModel, BeforeToolSelection, AfterTool, SessionStart, SessionEnd, Notification, PreCompress` [V, `packages/core/src/hooks/types.ts` HookEventName].
- Subagent start/stop events: NONE (not in the enum) [V].

**Subagents.** Yes: built-in `codebase_investigator`, CLI help agent, generalist, browser agent; custom agents as markdown files with frontmatter; exposed to the main agent as a tool of the same name (`docs/core/subagents.md`) [V].

**Permissions.** `default | auto_edit | yolo | plan`; TOML policy engine with rules per tool and mode (`docs/reference/policy-engine.md`) [V].

---

## 4. Cline (CLI)

Source: https://github.com/cline/cline, CLI at `apps/cli`, agent SDK at `sdk/` (clone `~/Desktop/harness-vendor/cline`).

**Release, license, language.** Repo has several tag families (`v4.1.25` VS Code extension, `desktop-v0.0.48`, `sdk/sdk/v0.0.95`, `cli-v3.0.73`). The terminal harness is `cli-v3.0.73` (2026-10-17) [V]. SHA `c718573538d76310f464541617ab665108a6c999` [V]. Apache-2.0, TypeScript (Bun, native binaries) [V].

**Install, pin.** `npm install -g cline@3.0.73` (`npm view` confirmed; `apps/cli/README.md`: platform binaries via optional deps, no Node needed) [V].

**Headless.** `cline --yolo --json "prompt"` runs one turn and exits (`apps/cli/README.md` Modes; `docs/cli/cli-reference.mdx`). `--auto-approve <bool>` (default true), `-t/--timeout <s>`, `--retries`, `-c/--cwd`, `--config <dir>` (or `CLINE_DIR`), `-P/--provider`, `-m/--model`, stdin piping [V]. NDJSON lines have `type` and `ts`: `agent_event` (content_start/update/end, iteration_start/end, usage, notice, done, error), `hook_event`, `team_event`, `run_result` (finishReason, iterations, usage, durationMs, model, text), `run_aborted`; errors on stderr as `{"type":"error",...}` (cli-reference.mdx lines 339-358) [V]. README says `--yolo` enables `submit_and_exit` and disables spawn/team tools by default [V].

**Custom endpoint.** Built-in provider ids `openai-compatible` (env `OPENAI_API_KEY`, default base `https://api.openai.com/v1`), `litellm` (env `LITELLM_API_KEY`, default `http://localhost:4000/v1`, Chat Completions), and `groq` (`sdk/packages/llms/src/providers/builtins.ts` lines 798, 844, 880) [V]. Non-interactive setup: `cline auth -p litellm -k "$KEY" -m <model-id> -b http://127.0.0.1:4000/v1` (flags `-p -k -m -b` from cli-reference.mdx lines 120-131) then `cline -P litellm -m <model-id> --yolo --json "..."` [V]. Tool calling with Groq models through LiteLLM is [U].

**Built-in tools** (`docs/tools-reference/all-cline-tools.mdx`; schemas in `sdk/packages/core/src/extensions/tools/schemas.ts`) [V]:
- `run_commands` (shell): `{commands: string[]}` (a structured `{command, args?}` form is also accepted).
- `editor` (edit AND create): `{path, old_text?, new_text, insert_line?}`; creates the file when it is missing.
- `apply_patch`: `{input: string}` in the `*** Begin Patch` grammar.
- there is no separate `write` tool; creation goes through `editor`.
- others: `read_files, search_codebase, fetch_web_content, skills, ask_question, submit_and_exit`, `spawn_agent`, `team_*`.

**Hook/plugin API** (two layers) [V].
1. External command hooks, files named by event in a hooks directory: `~/Documents/Cline/Hooks`, `~/.cline/hooks`, `<ws>/.cline/hooks`, plus the deprecated workspace dir; `--hooks-dir <path>` sets `CLINE_HOOKS_DIR` (`sdk/packages/shared/src/storage/paths.ts` lines 495-507; `apps/cli/src/main.ts` line 782; `sdk/packages/core/src/hooks/hook-file-config.ts`). Names: `PreToolUse, PostToolUse, UserPromptSubmit, TaskStart, TaskResume, TaskCancel, TaskComplete, TaskError, PreCompact, SessionShutdown`; extensions `.sh .py .js .ts .ps1` etc. Wired in `services/local-runtime-bootstrap.ts` line 434.
   - Pre-tool: `PreToolUse` runs blocking with a 120000 ms default timeout (`hook-file-hooks.ts` line 853). stdin JSON has `hookName:"tool_call"`, `tool_call:{id,name,input}`, `preToolUse:{toolName, parameters}` (parameters stringified), `taskId, agent_id, parent_agent_id, workspaceRoots, userId, clineVersion`. stdout JSON `{"cancel":true,"errorMessage":"..."}` blocks (reason goes back to the model), `contextModification` injects text, `overrideInput` rewrites input, `review:true` asks (`HookOutputSchema`, `subprocess.ts`). The CLI docs state: "To block commands, use a PreToolUse hook that cancels `run_commands` calls" (cli-reference.mdx line 335) [V].
   - Stop-block: NO. `TaskComplete` (agent_end) runs from `afterRun` as fire-and-forget (detached by default) and cannot refuse the stop (`hook-file-hooks.ts` lines 902-921, 1049-1062) [V].
2. In-process plugins (`docs/sdk/plugins.mdx` lines 186-200; modules under `.cline/plugins`): `hooks.beforeRun, afterRun, beforeModel, afterModel, beforeTool, afterTool, onEvent`. `beforeTool` returns `{skip:true, reason}` to block, `{policy:{autoApprove:false}}` to require approval, `input` to rewrite, `stop` to end the run. `afterRun` returns nothing, so it cannot refuse a stop; `afterModel`/`beforeModel` can only `stop` [V].
   - FAIL-OPEN WARNING: plugin hooks time out after 3 s, and default `hookErrorMode` is `"ignore"`, so a crashing or slow plugin hook lets the call proceed (plugins.mdx lines 203-213; `sdk/packages/core/src/types/config.ts`) [V]. A safety hook should be a file hook (120 s) or use `hookErrorMode:"throw"` [U for how to set it from the CLI].
- Subagent start/stop events: none as hooks. Payloads carry `agent_id` and `parent_agent_id`, so subagent tool calls are identifiable [V]; whether file hooks fire for subagent calls was not run [U].

**Subagents.** Yes: `spawn_agent` (`sdk/packages/core/src/extensions/tools/team/spawn-agent-tool.ts`) and agent teams (`team_*` tools, `docs/cli/agent-teams.mdx`) [V].

**Permissions.** `--auto-approve true|false`, `--yolo`, plan mode `-p`; tool policies per tool in config (`toolPolicies`, plugins.mdx line 134); no shell allow/deny list ("The CLI has no allow or deny list for shell commands") [V].

---

## 5. Open Interpreter (Codex fork, Rust)

Source: https://github.com/openinterpreter/openinterpreter (clone `~/Desktop/harness-vendor/open-interpreter`).

**Release, license, language.** Latest release `rust-v0.0.56` (2026-10-07). SHA `cc054cf52fa3585a3de50e0d4e0be6f9ee6677e8` (`rev-parse HEAD` equals `rev-parse rust-v0.0.56^{commit}`; the API tag-object sha 3f17ff19 is the annotated tag, not the commit) [V]. Apache-2.0, Rust workspace `codex-rs/` [V]. It is a fork of OpenAI Codex with a "harness emulation" layer (`docs/harness.md`: native, claude-code, claude-code-bare, zcode, kimi-code, kimi-cli, qwen-code, deepseek-tui, swe-agent, minimal) [V]. Integration cost is close to Codex, but it is a different binary with a different home (`~/.openinterpreter`, env `INTERPRETER_HOME`) and config.

**Install, pin.** `curl -fsSL https://www.openinterpreter.com/install | sh`; the installer reads `OPEN_INTERPRETER_RELEASE` (default `latest`) and `OPEN_INTERPRETER_NONINTERACTIVE=1` (`scripts/install/install.sh` lines 5-6, 98-99), so pin with `OPEN_INTERPRETER_RELEASE=rust-v0.0.56` [V for variables; whether the value takes the prefixed or bare form is U]. The release also ships `open-interpreter-package-x86_64-unknown-linux-musl.tar.gz` (GitHub release assets) for a direct pinned download [V].

**Headless.** `interpreter exec "prompt"` (`docs/exec.md`): `--json` JSONL events, `--output-last-message/-o FILE`, `--output-schema`, `--sandbox read-only|workspace-write|danger-full-access`, `--ask-for-approval/-a untrusted|on-request|never`, `--yolo` (bypass approvals and sandbox), `--ephemeral`, `--skip-git-repo-check`, `--ignore-user-config`, `--profile`, `--verify` (extra completion-check turn), `--chat-completions` (`docs/cli-reference.md` lines 19-30) [V]. Exit 0 means only that the turn completed; check outputs yourself (exec.md) [V].

**Custom endpoint.** `~/.openinterpreter/config.toml` (`docs/providers.md` lines 84-135) [V]:
```toml
model_provider = "litellm"
model = "<model-id>"
[model_providers.litellm]
name = "LiteLLM"
base_url = "http://127.0.0.1:4000/v1"
env_key = "LITELLM_KEY"
wire_api = "chat"
```
`wire_api = "chat"` means OpenAI Chat Completions; `--chat-completions` forces it for one run. A built-in `groq` provider (`GROQ_API_KEY`, wire chat) also exists (providers.md line 181) [V]. Tool calls over chat wire are in the maintainer checklist ("tool calls, tool results", open-interpreter-delta.md) [V]; Groq models through LiteLLM are [U].

**Built-in tools.** Native surface keeps Codex names: shell via `exec_command {cmd, ...}` surfaced to hooks as `Bash` with `tool_input:{command}`; file edits via `apply_patch` (hook `tool_input:{command: <patch text>}`; matcher aliases `Write` and `Edit` select it); `spawn_agent` (alias `Agent`) [V, `codex-rs/core/src/tools/hook_names.rs`, `handlers/apply_patch.rs` line 459, `handlers/unified_exec/exec_command.rs` line 520]. With `harness="claude-code"` the model sees Claude-style Bash, Read, Write, Edit, Glob, Grep, TodoWrite and subagent tools (harness.md) [V]; which hook name those emit [U]. There is no standalone `write` in the native surface.

**Hook API** (`docs/hooks.md`; schemas `codex-rs/hooks/schema/generated/*.json`; code `codex-rs/hooks/src/events/`) [V]. External commands, `~/.openinterpreter/hooks.json` or `config.toml`, project `.openinterpreter/hooks.json` (trusted projects), plugin-bundled; on by default (`[features] hooks = true`). Matchers are regexes. Events: `SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest, PostToolUse, PreCompact, PostCompact, SubagentStart, SubagentStop, Stop` (+ `SessionEnd`, `Interrupt` schemas).
- TRUST GATE: non-managed command hooks must be reviewed through `/hooks` and trusted before they run; automation can pass `--dangerously-bypass-hook-trust`. A headless harness must use that flag or pre-trust the hook, otherwise the hook may silently not run [V from doc; exact headless behavior U].
- Pre-tool: stdin JSON `session_id, cwd, hook_event_name, model, permission_mode, tool_name, tool_input, tool_use_id, turn_id, transcript_path, agent_id, agent_type`. Deny by `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"..."}}` or by exit 2 with the reason on stderr (exit 2 with empty stderr counts as a failed hook, not a block); any other exit code is a failed hook and the call proceeds (`events/pre_tool_use.rs` lines 261-290) [V]. Covers "supported shell, patch, or MCP tools" only (hooks.md), so reads and other built-ins are not covered [V]. Output schema also has `continue`, `decision`, `reason`, `stopReason`, `systemMessage`. An `ask` value: `PermissionRequest` hook runs before an approval prompt [V]; its exact allow/deny/ask output [U].
- Stop-block: YES. `Stop` input has `last_assistant_message`, `stop_hook_active`, `turn_id`. Output `{"decision":"block","reason":"..."}` or exit 2 with the continuation prompt on stderr makes the agent continue with that text; exit 2 without stderr is a hook failure (`events/stop.rs` lines 296-362, 419-444; stop output schema `decision` enum is `["block"]`) [V].
- Subagent: `SubagentStart` and `SubagentStop` exist, and `SubagentStop` can also block with the same exit-2 path (`stop.rs` line 362) [V]. `agent_id` and `agent_type` appear in tool-hook input [V].

**Subagents.** Yes: `features.multi_agent` on by default, `/agent` (`docs/config-reference.md` line 39) [V].

**Permissions.** `--ask-for-approval untrusted|on-request|never` plus sandbox modes; `--yolo` bypasses both; execpolicy rules (`docs/execpolicy.md`) [V].

---

## 6. goose (Rust)

Source: https://github.com/aaif-goose/goose (block/goose moved here) (clone `~/Desktop/harness-vendor/goose`).

**Release, license, language.** Latest release `v1.56.0` (2026-10-23). SHA `9949cf926cd42f1d128b9991ed23a496ecace896` [V]. Apache-2.0, Rust workspace `crates/` (CLI in `crates/goose-cli`) [V].

**Install, pin.** Release tarballs, for example `goose-x86_64-unknown-linux-gnu.tar.gz` from the `v1.56.0` release [V, release assets]; or `curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash` with `GOOSE_VERSION=v1.56.0` (download_cli.sh lines 18, 74-76; README.md line 38) [V].

**Headless.** `goose run -t "prompt"` (also `-i FILE`, `--recipe`) with `--no-session`, `--max-turns N` (default 1000), `-q/--quiet`, `--output-format text|json|stream-json`, `--provider`, `--model` (`documentation/docs/guides/goose-cli-commands.md` lines 428-456) [V]. stream-json emits events as they occur; the exact event schema was not read [U].

**Custom endpoint.** OpenAI provider with env `OPENAI_API_KEY`, `OPENAI_HOST` (host root, no trailing path), `OPENAI_BASE_PATH` (default `v1/chat/completions`); `GOOSE_PROVIDER=openai`, `GOOSE_MODEL=<model-id>` (`documentation/docs/getting-started/providers.md` lines 319-349; `guides/environment-variables.md` lines 20-30) [V]. Keys are not read from `config.yaml`, only from the environment or keyring (`guides/config-files.md` line 238) [V]. A built-in Groq provider exists (`GROQ_API_KEY`) [V]. Tool calling with Groq models through LiteLLM is [U]; goose has a tool-shim option for models without native tool calling (`guides/tool-shim.md`) [V].

**Built-in tools** (hooks.md "Tool Input Keys") [V]: `shell {command, timeout_secs?}`, `write {path, content}`, `edit {path, before, after}`, `tree {path, depth}`, `read_image`; other extensions are namespaced `ext__tool`.

**Hook API** (`documentation/docs/guides/context-engineering/hooks.md`; code `crates/goose/src/hooks/mod.rs`) [V]. Open Plugins style: a plugin directory with `plugin.json` and `hooks/hooks.json` under `~/.agents/plugins/<name>/` or `<project>/.agents/plugins/<name>/`; actions `{type:"command", command, timeout (default 30 s), on_failure}`; matcher is a regex (a bare `*` is an invalid regex and the rule is silently skipped); command run with `sh -c`, env `PLUGIN_ROOT`. stdin JSON: `event, session_id, matcher_context, tool_name, tool_input, working_dir, tool_call_id, message, last_assistant_message`.
- Pre-tool: `PreToolUse` (matcher is the tool name). Block by exit 2 (reason from stderr) or stdout `{"decision":"block","reason":"..."}` (honored whatever the exit code). Exit 0 with empty stdout or `{"decision":"allow"}` allows. Anything else (stray stdout line, malformed JSON, other exit codes, timeout, delivery failure) reads as "no decision"; DEFAULT is fail-open (tool proceeds). Set `"on_failure":"block"` on the action to fail closed (PreToolUse only). The model sees: "Tool call denied by policy hook ... Do not retry". `PreToolUseResult` is an observation-only follow-up event. No "ask" value [V].
- Stop-block: YES. "A `Stop` hook that blocks forces the turn to keep going"; consecutive blocks are capped (env `GOOSE_STOP_HOOK_BLOCK_CAP`) and the cap then overrides the hook; Stop stays fail-open even with `on_failure:block`. The block signal uses the same decision channel as PreToolUse (the doc says "PreToolUse and Stop can block") [V]; the exact accepted JSON for Stop was inferred from that shared channel [U].
- Subagent events: `SubagentStart`/`SubagentStop` are NOT emitted (hooks.md troubleshooting line, explicit). A subagent emits `SessionEnd` with its own session id (`crates/goose/src/agents/subagent_handler.rs` lines 225-229) [V]. Subagent tool calls pass through the same state machine, so PreToolUse should apply [U].
- Other events: `SessionStart, SessionEnd, UserPromptSubmit, PostToolUse, PostToolUseFailure, BeforeReadFile, AfterFileEdit, BeforeShellExecution, AfterShellExecution` [V].

**Subagents.** Yes: natural-language delegation, parallel or sequential, recipes as subagents (`guides/context-engineering/subagents.mdx`), `summon` extension [V].

**Permissions.** `GOOSE_MODE` = `auto` (default, no approvals) | `approve` | `smart_approve` | `chat` (`guides/environment-variables.md` line 256; `guides/managing-tools/goose-permissions.md`) [V]. Headless unattended run uses the default `auto`.

---

## 7. Summary table

| Harness | Tag | SHA | Headless command | Custom OpenAI-compatible endpoint | Pre-tool block | Stop-block | Subagents |
|---|---|---|---|---|---|---|---|
| OpenCode | v1.19.4 | 148842ef7cb1bc34192b3fff57d0f9721ab9c801 | `opencode run --format json --auto -m litellm/<id> "..."` | Yes: `@ai-sdk/openai-compatible` provider, `options.baseURL`, `apiKey:"{env:VAR}"` | Yes: in-process plugin `tool.execute.before`, throw to block (can rewrite `output.args`); no ask value; `permission.ask` hook declared but never called | No (observe-only `session.idle` event; re-prompt through the SDK is a workaround) | Yes (`task` tool); no start/stop hook, child sessions have `parentID` |
| Gemini CLI | v0.65.0 | 6006c14a4decfe4b891c6263ad6ad5992e404b5b | `gemini -p "..." --output-format stream-json --approval-mode yolo` | Gemini wire format only: `GOOGLE_GEMINI_BASE_URL` plus `GEMINI_API_KEY`, needs LiteLLM Gemini pass-through with model alias (Groq tool calls U) | Yes: `BeforeTool` command hook, `decision:"deny"` or exit 2, can rewrite `tool_input`; other exit codes fail open | Yes: `AfterAgent` `decision:"deny"` or exit 2 sends `reason` as a new prompt, `stop_hook_active` guard | Yes (built-in and custom agents); no subagent hook events |
| Cline CLI | cli-v3.0.73 | c718573538d76310f464541617ab665108a6c999 | `cline --yolo --json -P litellm -m <id> "..."` | Yes: `litellm` and `openai-compatible` providers, `cline auth -p ... -k ... -m ... -b URL` | Yes: file hook `PreToolUse` stdout `{"cancel":true,"errorMessage":...}` (120 s timeout) or plugin `beforeTool` `{skip:true}` / `{policy:{autoApprove:false}}`; plugin hooks time out at 3 s and fail open by default | No (`TaskComplete` and `afterRun` are fire-and-forget) | Yes (`spawn_agent`, teams); no hook events, payload has `parent_agent_id` |
| Open Interpreter | rust-v0.0.56 | cc054cf52fa3585a3de50e0d4e0be6f9ee6677e8 | `interpreter exec --json --yolo --dangerously-bypass-hook-trust "..."` (add `--chat-completions`) | Yes: `[model_providers.x]` `base_url`, `env_key`, `wire_api="chat"` in `~/.openinterpreter/config.toml` | Yes: `PreToolUse` command hook, `permissionDecision:"deny"` or exit 2 with stderr; covers shell, `apply_patch`, MCP only; other exit codes fail open; hook trust gate | Yes: `Stop` hook `decision:"block"` or exit 2 with stderr prompt | Yes (`spawn_agent`); `SubagentStart` and `SubagentStop` hooks exist, `SubagentStop` can block |
| goose | v1.56.0 | 9949cf926cd42f1d128b9991ed23a496ecace896 | `goose run -t "..." --no-session --output-format stream-json` | Yes: OpenAI provider, `OPENAI_HOST`, `OPENAI_BASE_PATH`, `OPENAI_API_KEY`, `GOOSE_PROVIDER`/`GOOSE_MODEL` | Yes: `PreToolUse` command hook, exit 2 or stdout `{"decision":"block"}`; fail-open by default, `on_failure:"block"` fails closed | Yes: `Stop` hook block forces the turn to continue, capped by `GOOSE_STOP_HOOK_BLOCK_CAP` | Yes; `SubagentStart`/`SubagentStop` are not emitted (subagent emits `SessionEnd`) |

## 8. Notes for the benchmark design

- Pre-tool block with a verdict the hook returns: only goose, Gemini CLI, Open Interpreter and Cline file hooks take an external stdin-JSON script. OpenCode needs an in-process JS plugin (throw to block). One shared policy script can serve the four command-hook harnesses behind thin adapters; each speaks a different stdout shape.
- Fail-open defaults on four of five (goose, Gemini CLI exit codes other than 2, Open Interpreter, Cline plugins): a crashing safety hook lets the call through. Test hook-crash and hook-timeout cases per harness, and check from the transcript that the hook ran.
- Stop-refusal exists on Gemini CLI, Open Interpreter and goose only. OpenCode and Cline lack it.
- Subagent start/stop hook events exist only on Open Interpreter. goose says they are not emitted.
- Gemini CLI is the integration risk: Gemini wire format via LiteLLM translation, plus the README retirement banner.
- Run-time behavior (a thrown plugin error in OpenCode, headless hook firing, Groq tool calls through LiteLLM) was not exercised; this report is from source and docs only.
