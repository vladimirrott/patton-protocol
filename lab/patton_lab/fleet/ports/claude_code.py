"""The fleet runner's port of Claude Code 2.1.310 with the real patton-gate plugin (see ../spec.py for the contract,
harness/claude-code/RESULT.md for the trace).

One run is one container: the lab's pinned python image (python3 3.12 for the plugin's hooks), `--network=none`, no
capabilities, a read-only root, git from `harness/gitmount.py` (the P1 work dir is a repository and the Verifier
snapshots it), and the pinned Claude Code binary, a copy kept at lab/harness_cache/claude-code/2.1.310/claude and
checked by sha256, mounted read-only at /opt/claude/claude. The only way out is OpenCode's relay
(`harness/opencode/relay.py`): the host serves a unix socket that forwards to the recorder on
127.0.0.1:`spec.model_port`, and inside the container the relay listens on 127.0.0.1:18080. So ANTHROPIC_BASE_URL is
`http://127.0.0.1:18080` (the recorder serves `/v1/messages`, WIRE is "anthropic", and answers count_tokens itself),
and "{model_base}" in the gate's config becomes the same address, which puts the Verifier's calls on the recorder's
`/t1/v1/chat/completions` route through the same socket.

Claude Code is pointed at the run's model the way the gate study did it (patton_lab/cc/runner.py): a placeholder
ANTHROPIC_AUTH_TOKEN, every ANTHROPIC_DEFAULT_*_MODEL and ANTHROPIC_SMALL_FAST_MODEL set to `spec.model` (side
calls go to the same model and are metered like the rest), CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC and
CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS on, the autoupdater off. CLAUDE_CODE_MODEL_CAPABILITIES="-mid_conv_system"
turns off the mid-conversation `role: "system"` messages 2.1.310 sends an unknown model by default: the plugin's
PreToolUse reminder and the "Environment" block went out only that way, and LiteLLM 1.92.0's /v1/messages adapter
drops such messages on the way to Groq (checked offline, RESULT.md), so the reminder would never reach gpt-oss.
With it off, both travel as text blocks in user messages, which LiteLLM keeps. No Anthropic key or login exists in the container:
HOME is the run's fresh `spec.home`, and CLAUDE_CONFIG_DIR is inside it. `--permission-mode bypassPermissions`
turns Claude Code's own prompts off; `--setting-sources user` on that empty home loads no settings, project
settings in /work never load, `--strict-mcp-config` loads no MCP server, and WebFetch and WebSearch are disallowed.

A guarded arm loads plugins/patton-gate (0.4.0), mounted read-only at /opt/patton-gate, with `--plugin-dir`, and
sets PATTON_GATE_ROOT, PATTON_GATE_STATE, PATTON_GATE_CONFIG and every `spec.env` variable in Claude Code's
environment, which its hooks inherit. Discipline is native: the plugin's PreToolUse carries the test reminder as
`additionalContext` (CONTEXT is True) and its Stop hook refuses a stop with a reason Claude Code hands the model
(STOP is True). The unguarded arm mounts no plugin, no config and sets no PATTON_* variable.

The final text is the `result` field of stream-json's closing `result` event, else the last assistant text block.
The exit code is reported, never used to judge. Every container is named `pg-fleet-claude-code-<hex>` and labelled
`patton-fleet=claude-code`; a run that outlives the host timeout removes its own container by that name only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from ... import isolate
from ...harness import gitmount
from ...harness.opencode import relay as relay_mod
from .. import spec as fleet_spec

VERSION = "2.1.310"
WIRE = "anthropic"
STOP = True      # the plugin's Stop hook refuses through Claude Code's own `decision: block` (trace, stop arm)
CONTEXT = True   # PreToolUse `additionalContext` reaches the model's next request (trace, jit arm)
LABEL = "patton-fleet=claude-code"
LAB = Path(__file__).resolve().parents[3]
REPO = LAB.parent
BINARY = LAB / "harness_cache" / "claude-code" / VERSION / "claude"
BINARY_SHA256 = "b73676430af12f97316a798b8371f81d640f90a58bdcb8f3ce56a835bb47df56"
BINARY_IN = "/opt/claude/claude"
PLUGIN = REPO / "plugins" / "patton-gate"
PLUGIN_IN = "/opt/patton-gate"
RELAY = Path(relay_mod.__file__).resolve()
RELAY_IN = "/kit/relay.py"
PORT_IN = 18080
MODEL_BASE = f"http://127.0.0.1:{PORT_IN}"
CONFIG_IN = "/patton-config/config.json"
# Turns off Claude Code's mid-conversation system messages for every model, so hook context and hook refusals
# travel in user messages, which LiteLLM's /v1/messages keeps (it drops system ones).
CAPABILITIES = "-mid_conv_system"
PATH_IN = "/opt/claude:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"


@lru_cache(maxsize=1)
def _binary_ok() -> bool:
    return hashlib.sha256(BINARY.read_bytes()).hexdigest() == BINARY_SHA256


def _podman_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "XDG_RUNTIME_DIR", "LANG", "USER", "LOGNAME", "TMPDIR")
    return {k: os.environ[k] for k in keep if k in os.environ}


def available() -> str | None:
    if not BINARY.is_file():
        return (f"the pinned Claude Code {VERSION} binary is not at lab/harness_cache/claude-code/{VERSION}/claude "
                "(see harness/claude-code/RESULT.md)")
    if not _binary_ok():
        return f"lab/harness_cache/claude-code holds a binary that is not the pinned Claude Code {VERSION} build"
    why = gitmount.why_not()
    if why:
        return why
    if shutil.which("podman") is None:
        return "podman is not installed"
    try:
        found = subprocess.run(["podman", "--log-level", "error", "image", "exists", isolate.IMAGE],
                               capture_output=True, timeout=60, env=_podman_env())
    except (OSError, subprocess.SubprocessError) as err:
        return f"podman is not usable ({type(err).__name__})"
    return None if found.returncode == 0 else "the pinned lab image is not pulled"


def shell_step(command: str) -> dict[str, Any]:
    return {"tool": "Bash", "input": {"command": command, "description": "run"}}


def prepare_config(run_spec: fleet_spec.RunSpec) -> None:
    """Fill "{model_base}" in the gate's config file with the recorder's address as the container sees it."""
    text = run_spec.config_file.read_text(encoding="utf-8").replace("{model_base}", MODEL_BASE)
    json.loads(text)  # loud: a config the gate cannot read must not start a run
    run_spec.config_file.write_text(text, encoding="utf-8")


def podman_argv(run_spec: fleet_spec.RunSpec, name: str, relay_dir: Path) -> list[str]:
    model = run_spec.model
    env = {"HOME": "/home/user", "CLAUDE_CONFIG_DIR": "/home/user/.claude", "PATH": PATH_IN, "LANG": "C.UTF-8",
           "TERM": "dumb", "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1",
           "ANTHROPIC_BASE_URL": MODEL_BASE, "ANTHROPIC_AUTH_TOKEN": "placeholder",
           "ANTHROPIC_DEFAULT_OPUS_MODEL": model, "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": model, "ANTHROPIC_SMALL_FAST_MODEL": model,
           "CLAUDE_CODE_MODEL_CAPABILITIES": CAPABILITIES,
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
           "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"}
    mounts = [(run_spec.work, "/work:rw"), (run_spec.state, "/patton-state:rw"), (run_spec.home, "/home/user:rw"),
              (relay_dir, "/relay:ro"), (RELAY, f"{RELAY_IN}:ro"), (BINARY, f"{BINARY_IN}:ro")]
    mounts += gitmount.mounts()
    harness = [BINARY_IN, "-p", run_spec.mission, "--model", model, "--output-format", "stream-json", "--verbose",
               "--permission-mode", "bypassPermissions", "--no-session-persistence", "--setting-sources", "user",
               "--strict-mcp-config", "--include-hook-events", "--disallowedTools", "WebFetch", "WebSearch"]
    if run_spec.gate_config is not None:
        env.update({"PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": "/patton-state",
                    "PATTON_GATE_CONFIG": CONFIG_IN, **run_spec.env})
        mounts += [(run_spec.config_file, f"{CONFIG_IN}:ro"), (PLUGIN, f"{PLUGIN_IN}:ro")]
        harness += ["--plugin-dir", PLUGIN_IN]
    argv = ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", name, "--label", LABEL,
            "--userns=keep-id", "--network=none", "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only",
            "--tmpfs", "/tmp:size=256m", "--memory=4g", "--pids-limit=1024"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    for host, inside in mounts:
        argv += ["-v", f"{host}:{inside}"]
    return argv + ["-w", "/work", isolate.IMAGE, "timeout", "-k", "5", str(run_spec.timeout_s),
                   "python3", "-I", RELAY_IN, "/relay/chat.sock", str(PORT_IN), "--", *harness]


def final_text(stdout: str) -> str:
    """The closing `result` event's text in Claude Code's stream-json, else the last assistant text block."""
    result, last = None, ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            result = event["result"]
        elif event.get("type") == "assistant" and isinstance(event.get("message"), dict):
            texts = [b.get("text") for b in event["message"].get("content") or []
                     if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
            if any(t.strip() for t in texts):
                last = "\n".join(texts)
    return result if result is not None else last


def _text(value: Any) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


def run(run_spec: fleet_spec.RunSpec) -> fleet_spec.RunResult:
    if run_spec.gate_config is not None:
        prepare_config(run_spec)
    name = f"pg-fleet-claude-code-{uuid.uuid4().hex[:12]}"
    # A short directory of its own: a unix socket path must stay under 108 bytes.
    relay_dir = Path(tempfile.mkdtemp(prefix="pg-cc-"))
    host_relay = None
    started = time.time()
    try:
        host_relay = relay_mod.HostRelay(str(relay_dir / "chat.sock"), run_spec.model_port)
        try:
            done = subprocess.run(podman_argv(run_spec, name, relay_dir), capture_output=True, text=True,
                                  timeout=run_spec.timeout_s + 60, stdin=subprocess.DEVNULL, env=_podman_env())
            rc, stdout, stderr = done.returncode, done.stdout, done.stderr
            # timeout(1) exits 124 when it stopped the harness and 137 when it had to kill it.
            timed_out = rc in (124, 137)
        except subprocess.TimeoutExpired as err:
            subprocess.run(["podman", "--log-level", "error", "rm", "-f", "-t", "0", name], capture_output=True,
                           timeout=60, env=_podman_env())
            rc, stdout, stderr, timed_out = None, _text(err.stdout), _text(err.stderr) + "\nhost timeout", True
    finally:
        if host_relay is not None:
            host_relay.close()
        shutil.rmtree(relay_dir, ignore_errors=True)
    return fleet_spec.RunResult(rc, timed_out, final_text(stdout), round(time.time() - started, 1),
                                stdout[-4000:], stderr[-4000:])
