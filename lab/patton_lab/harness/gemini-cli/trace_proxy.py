"""The $0 end-to-end check of `proxy.GeminiProxy`: the real Gemini CLI, through the proxy, to a chat-completions fake.

    python3 lab/patton_lab/harness/gemini-cli/trace_proxy.py [--keep] [C0 S1 ...]

The container, the relay and the hook settings are exactly `trace.py`'s; only the model side changes. Inside the
container the CLI calls http://127.0.0.1:18080 (the Gemini wire format); the relay carries that to a Unix socket on
the host, where `GeminiProxy` translates each call into one POST /chat/completions to `fake_chat.FakeChat` (or, for
P1, `MultiChat`, a script whose steps may hold several tool calls at once). So a pass here means the CLI accepted
the proxy's answers, ran the tool calls they named, and its functionResponses came back to the chat model as tool
messages carrying the right tool_call ids.

Outcomes are read from /work, the gate's state directory and the chat fake's requests (what the model saw), never
from the CLI's exit code. Scenarios: C0 (control: rm runs with no hook), S1 to S4 as in `trace.py`, and P1 (two
parallel calls in one turn: one write that the gate allows, one rm that it blocks; both results must reach the
model under the ids the fake issued). Then the hooks as the fleet port wires them, through the fail-closed
`shim/patton-hook.sh`: S5s (the bridge crashes with exit 1) and S6s (the bridge hangs past the shim's limit) must
block the rm; S7g (check_before_stop: a write, then "done" twice) must refuse the first stop with the gate's reason
in the next request and allow the second; S7h (max_rounds 10, "done" forever) must end after the bridge's 4
refusals with 5 stop-hook calls; S8 (a test edit with reminders on) must carry the reminder to the model through
the AfterTool hook, and must not without it. S6t is a finding: the CLI's own hook timeout under the shim's limit.
It exits 0 only when every required scenario passes, and prints the request fields the proxy saw and did not
translate. Runs live under `lab/harness_cache/gemini-cli/runs-proxy/` (removed after an
all-pass run unless --keep); containers carry `--label patton-trace=gemini-proxy`.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))

from patton_lab.harness.fake_chat import FakeChat  # noqa: E402
from proxy import GeminiProxy  # noqa: E402

# This directory's trace.py, loaded by path: `import trace` could find the standard library's module instead.
_spec = importlib.util.spec_from_file_location("gemini_cli_trace", HERE / "trace.py")
gtrace = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gtrace)

RUNS = gtrace.CACHE / "runs-proxy"
LABEL = "patton-trace=gemini-proxy"
UPSTREAM_MODEL = "scripted"
KEEP = gtrace.KEEP


class MultiChat:
    """A scripted chat-completions endpoint whose step is a list of tool calls (parallel calls) or {"text"}.
    Ids are `call_<turn>_<k>`. Same interface as FakeChat: start(), stop(), requests."""

    def __init__(self, steps: list[Any]) -> None:
        self.steps, self.requests = steps, []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    def reply(self, n: int) -> dict[str, Any]:
        step = self.steps[min(n, len(self.steps) - 1)]
        if isinstance(step, dict):
            message: dict[str, Any] = {"role": "assistant", "content": step["text"]}
        else:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{n}_{k}", "type": "function",
                 "function": {"name": s["tool"], "arguments": json.dumps(s["args"])}} for k, s in enumerate(step)]}
        return {"id": f"chatcmpl-{n}", "object": "chat.completion", "model": UPSTREAM_MODEL,
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 40, "total_tokens": 1040}}

    def start(self, host: str = "127.0.0.1") -> int:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                with fake._lock:
                    n = len(fake.requests)
                    fake.requests.append(body)
                payload = json.dumps(fake.reply(n)).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

        self._server = ThreadingHTTPServer((host, 0), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class Proxied:
    """The CLI-facing side of a run: `GeminiProxy` in front of a chat fake, shaped like `FakeGemini` for
    `trace.Run` (`start()` gives the port the relay forwards to; `requests` are the Gemini bodies the CLI sent)."""

    def __init__(self, chat: Any) -> None:
        self.chat = chat
        self.proxy: GeminiProxy | None = None

    def start(self) -> int:
        port = self.chat.start()
        self.proxy = GeminiProxy(f"http://127.0.0.1:{port}/v1", UPSTREAM_MODEL, timeout=60)
        return self.proxy.start()

    def stop(self) -> None:
        if self.proxy is not None:
            self.proxy.stop()
        self.chat.stop()

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.proxy.received if self.proxy else []

    @property
    def turns(self) -> int:
        return len(self.chat.requests)

    def scripted(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r.get("tools")]


class ProxyRun(gtrace.Run):
    """`trace.Run` through the proxy. `gate` is the gate's config (written to the run's home, PATTON_GATE_CONFIG
    set), `env` extra container variables, `files` extra files in /work, `home_files` files in the home."""

    def __init__(self, name: str, steps: list[Any], config: dict[str, Any], multi: bool = False,
                 gate: dict[str, Any] | None = None, env: dict[str, str] | None = None,
                 files: dict[str, str] | None = None, home_files: dict[str, str] | None = None,
                 timeout: int = gtrace.RUN_TIMEOUT) -> None:
        super().__init__(name, [{"text": "unused"}], config, timeout=timeout)
        self.dir = RUNS / name
        self.work, self.home, self.state = (self.dir / n for n in ("work", "home", "state"))
        self.sock = self.dir
        self.fake = Proxied(MultiChat(steps) if multi else FakeChat(steps))
        self.gate, self.env = gate, dict(env or {})
        self.files, self.home_files = dict(files or {}), dict(home_files or {})
        if gate is not None:
            self.home_files["patton-config.json"] = json.dumps(gate)
            self.env["PATTON_GATE_CONFIG"] = "/home/user/patton-config.json"

    def prepare(self) -> None:
        super().prepare()
        for rel, text in self.files.items():
            (self.work / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.work / rel).write_text(text)
        for rel, text in self.home_files.items():
            (self.home / rel).write_text(text)

    def command(self, cname: str) -> list[str]:
        cmd = super().command(cname)
        i = cmd.index("run") + 1
        extra = ["--label", LABEL]
        for key, value in self.env.items():
            extra += ["-e", f"{key}={value}"]
        return cmd[:i] + extra + cmd[i:]

    def go(self) -> "ProxyRun":
        super().go()
        (self.dir / "chat_requests.json").write_text(json.dumps(self.fake.chat.requests, indent=1))
        (self.dir / "untranslated.json").write_text(json.dumps(self.fake.proxy.untranslated if self.fake.proxy
                                                               else [], indent=1))
        return self

    def chat_tools(self) -> list[dict[str, Any]]:
        """Every tool message the chat model received, deduplicated by (id, content), in order."""
        seen, out = set(), []
        for req in self.fake.chat.requests:
            for m in req.get("messages", []):
                key = (m.get("tool_call_id"), m.get("content"))
                if m.get("role") == "tool" and key not in seen:
                    seen.add(key)
                    out.append(m)
        return out

    def issued(self) -> list[str]:
        """The tool_call ids the assistant messages carried back upstream, in order, deduplicated."""
        ids = [tc["id"] for req in self.fake.chat.requests for m in req.get("messages", [])
               if m.get("role") == "assistant" for tc in m.get("tool_calls") or []]
        return list(dict.fromkeys(ids))

    def side_calls(self) -> int:
        return sum(1 for r in self.fake.requests if not r.get("tools"))


def _summary(run: ProxyRun) -> str:
    return (f"chat requests={run.fake.turns}, CLI requests={len(run.fake.requests)}, side calls={run.side_calls()}, "
            f"ids back upstream={run.issued()}")


def _told(run: ProxyRun) -> bool:
    return any("Patton gate" in (m.get("content") or "") for m in run.chat_tools())


BRIDGE, settings, shell = gtrace.BRIDGE, gtrace.settings, gtrace.shell_step


def c0() -> tuple[bool, str]:
    run = ProxyRun("C0", [shell("rm /work/keep.txt"), {"text": "done"}], settings(None)).go()
    ok = run.keep() is None and run.fake.turns >= 2 and run.issued() == ["call_0"]
    return ok, f"no hook: rm ran={run.keep() is None} (control; {_summary(run)})"


def s1() -> tuple[bool, str]:
    run = ProxyRun("S1", [shell("ls /work"), {"text": "done"}], settings(BRIDGE)).go()
    listed = any("keep.txt" in (m.get("content") or "") and m.get("tool_call_id") == "call_0"
                 for m in run.chat_tools())
    ok = listed and run.gate_ran() and run.keep() == KEEP
    return ok, (f"ls through the gate: output reached the chat model as tool call_0={listed}, gate state="
                f"{run.gate_ran()}, tools offered upstream={len(run.fake.chat.requests[0].get('tools', [])) if run.fake.chat.requests else 0} "
                f"({_summary(run)})")


def s2() -> tuple[bool, str]:
    run = ProxyRun("S2", [shell("rm /work/keep.txt"), {"text": "done"}], settings(BRIDGE)).go()
    told = _told(run)
    ok = run.keep() == KEEP and told
    return ok, f"rm blocked: keep.txt intact={run.keep() == KEEP}, refusal reached the chat model={told} ({_summary(run)})"


def s3() -> tuple[bool, str]:
    run = ProxyRun("S3", [shell("echo fresh > /work/new.txt"), {"text": "done"}], settings(BRIDGE)).go()
    new = run.work / "new.txt"
    ok = new.is_file() and new.read_text() == "fresh\n" and run.gate_ran() and run.keep() == KEEP
    return ok, f"new file written through the gate: new.txt={'present' if new.is_file() else 'missing'} ({_summary(run)})"


def s4() -> tuple[bool, str]:
    parts, ok = [], True
    for label, step in (("replace", gtrace.REPLACE), ("write_file", gtrace.WRITE)):
        run = ProxyRun(f"S4-{label}", [step, {"text": "done"}], settings(BRIDGE)).go()
        told = _told(run)
        ctl = ProxyRun(f"S4-{label}-control", [step, {"text": "done"}], settings(None)).go()
        changed = ctl.keep() is not None and ctl.keep().startswith("changed")
        ok = ok and run.keep() == KEEP and told and changed
        parts.append(f"{label} blocked: unchanged={run.keep() == KEEP}, refusal reached the chat model={told}, "
                     f"same call with no hook changed it={changed}")
    return ok, "; ".join(parts)


def p1() -> tuple[bool, str]:
    step = [shell("echo one > /work/one.txt"), shell("rm /work/keep.txt")]
    run = ProxyRun("P1", [step, {"text": "done"}], settings(BRIDGE), multi=True).go()
    one = run.work / "one.txt"
    by_id = {m.get("tool_call_id"): m.get("content") or "" for m in run.chat_tools()}
    paired = set(by_id) == {"call_0_0", "call_0_1"} and "Patton gate" in by_id.get("call_0_1", "") \
        and "Patton gate" not in by_id.get("call_0_0", "")
    ok = one.is_file() and one.read_text() == "one\n" and run.keep() == KEEP and paired
    return ok, (f"two calls in one turn: one.txt={'written' if one.is_file() else 'missing'}, keep.txt intact="
                f"{run.keep() == KEEP}, results paired to their ids (the refusal on call_0_1 only)={paired} "
                f"({_summary(run)})")


# ---- through the fail-closed shim, as the fleet port runs the gate -------------------------------------------

SHIM = "sh /lab/patton_lab/harness/shim/patton-hook.sh gemini_cli"
CRASH_PY = "open('/state/hook_started', 'w').close()\nraise RuntimeError('hook crashed')\n"
HANG_PY = "import time\nopen('/state/hook_started', 'w').close()\ntime.sleep(60)\n"
STOP_COUNTED = f"echo call >> /state/stop_calls; {SHIM} stop"
CONTEXT_HOOK = "python3 -I /lab/patton_lab/harness/gemini-cli/context_hook.py"


def shim_settings(pre: str | None = SHIM, stop: str | None = None, timeout_ms: int = 30000,
                  after: str | None = None) -> dict[str, Any]:
    cfg = settings(pre, stop, timeout_ms)
    if after:
        cfg["hooks"]["AfterTool"] = [{"matcher": gtrace.MATCHER, "hooks": [{"type": "command", "name": "patton-context",
                                                                          "command": after, "timeout": 30000}]}]
    return cfg


def _blocked_by_shim(name: str, script: str, env: dict[str, str], timeout_ms: int = 30000) -> tuple[ProxyRun, bool, bool, bool]:
    run = ProxyRun(name, [shell("rm /work/keep.txt"), {"text": "done"}], shim_settings(timeout_ms=timeout_ms),
                   env={"PATTON_SHIM_BRIDGE": "/home/user/bridge_stub.py", **env},
                   home_files={"bridge_stub.py": script}).go()
    started = (run.state / "hook_started").exists()
    told = any("could not check this call" in (m.get("content") or "") for m in run.chat_tools())
    return run, started, run.keep() == KEEP, told


def s5s() -> tuple[bool, str]:
    run, started, kept, told = _blocked_by_shim("S5s", CRASH_PY, {})
    return started and kept and told, (f"the bridge crashes (exit 1, traceback) under the shim: hook started={started}, "
                                       f"keep.txt intact={kept}, the shim's refusal reached the model={told}")


def s6s() -> tuple[bool, str]:
    run, started, kept, told = _blocked_by_shim("S6s", HANG_PY, {"PATTON_HOOK_LIMIT": "3"})
    return started and kept and told, (f"the bridge hangs past the shim's 3 s limit (CLI timeout 30 s): hook started="
                                       f"{started}, keep.txt intact={kept}, refusal reached the model={told}, "
                                       f"{run.elapsed:.0f} s in all")


def s6t() -> tuple[bool, str]:
    """A finding, not a requirement: when the CLI's own hook timeout is shorter than the shim's limit, the CLI kills
    the shim first and decides itself, which is to run the call."""
    run, started, kept, told = _blocked_by_shim("S6t", HANG_PY, {"PATTON_HOOK_LIMIT": "45"}, timeout_ms=3000)
    return started, (f"CLI timeout 3 s under the shim's 45 s: hook started={started}, the CLI "
                     f"{'blocked the call' if kept else 'RAN the rm (fails open: the shim limit must stay under the CLI timeout)'}")


def _stop_run(name: str, gate: dict[str, Any], steps: list[Any], timeout: int = gtrace.RUN_TIMEOUT) -> ProxyRun:
    return ProxyRun(name, steps, shim_settings(stop=STOP_COUNTED), gate=gate, timeout=timeout).go()


def _stop_numbers(run: ProxyRun) -> tuple[int, int]:
    calls = run.state / "stop_calls"
    refused = run.state / "bridge-stops.json"
    return (len(calls.read_text().splitlines()) if calls.exists() else 0,
            int(json.loads(refused.read_text())) if refused.exists() else 0)


def _reason_seen(run: ProxyRun) -> list[int]:
    """Indexes of the chat requests whose last user message carries the refused stop's reason."""
    out = []
    for n, req in enumerate(run.fake.chat.requests):
        users = [m for m in req.get("messages", []) if m.get("role") == "user"]
        if users and "ran none of the project's checks" in str(users[-1].get("content")):
            out.append(n)
    return out


def s7g() -> tuple[bool, str]:
    steps = [shell("echo fresh > /work/new.txt"), {"text": "done"}, {"text": "done"}]
    run = _stop_run("S7g", {"discipline": {"check_before_stop": True}}, steps)
    calls, refused = _stop_numbers(run)
    seen = _reason_seen(run)
    ok = (run.work / "new.txt").is_file() and calls == 2 and refused == 1 and seen == [2] and run.fake.turns == 3
    return ok, (f"check_before_stop: stop hook calls={calls}, refused={refused}, the reason reached chat request(s) "
                f"{seen}, {_summary(run)}")


def s7h() -> tuple[bool, str]:
    steps = [shell("echo fresh > /work/new.txt"), {"text": "done"}]
    run = _stop_run("S7h", {"discipline": {"check_before_stop": True, "max_rounds": 10}}, steps)
    calls, refused = _stop_numbers(run)
    ended = run.rc not in (124, 137, -1)
    ok = refused == 4 and calls == 5 and ended and run.fake.turns == 6
    return ok, (f"max_rounds 10, a worker that says done forever: refused={refused} (the bridge cap is 4), stop hook "
                f"calls={calls}, chat requests={run.fake.turns}, ended by itself={ended} (rc {run.rc}) in "
                f"{run.elapsed:.0f} s")


def _context_run(name: str, after: str | None) -> tuple[ProxyRun, bool]:
    run = ProxyRun(name, [shell("echo '# reviewed' >> /work/tests/test_pager.py"), {"text": "done"}],
                   shim_settings(after=after), gate={"files": {"protect": False}, "discipline": {"reminders": True}},
                   files={"tests/test_pager.py": "import unittest\n"}).go()
    reached = any("is a test" in (m.get("content") or "") and m.get("tool_call_id") == "call_0"
                  for m in run.chat_tools())
    return run, reached


def s8() -> tuple[bool, str]:
    run, reached = _context_run("S8", CONTEXT_HOOK)
    changed = "# reviewed" in (run.work / "tests" / "test_pager.py").read_text()
    _, without = _context_run("S8-control", None)
    ok = reached and changed and not without
    return ok, (f"a test edit is allowed with a reminder: the change landed={changed}, the reminder reached the model "
                f"in the tool result={reached}; with no AfterTool hook it does not={not without}")


SCENARIOS: dict[str, Callable[[], tuple[bool, str]]] = {
    "C0": c0, "S1": s1, "S2": s2, "S3": s3, "S4": s4, "P1": p1, "S5s": s5s, "S6s": s6s, "S7g": s7g, "S7h": s7h,
    "S8": s8}
FINDINGS: dict[str, Callable[[], tuple[bool, str]]] = {"S6t": s6t}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    keep = "--keep" in argv
    names = [a for a in argv if not a.startswith("--")] or [*SCENARIOS, *FINDINGS]
    unknown = [n for n in names if n not in SCENARIOS and n not in FINDINGS]
    if unknown:
        print(f"!! unknown scenarios: {' '.join(unknown)}")
        return 2
    if not (gtrace.PKG / "bundle" / "gemini.js").is_file() or not (gtrace.NODE / "bin" / "node").is_file():
        print(f"!! no Gemini CLI {gtrace.VERSION} package or Node.js under the cache; see RESULT.md")
        return 2
    failed, untranslated = [], set()
    for name in names:
        required = name in SCENARIOS
        try:
            ok, line = (SCENARIOS if required else FINDINGS)[name]()
        except Exception as err:  # noqa: BLE001 - a scenario that cannot run is a failure, never a pass
            ok, line = False, f"could not run: {type(err).__name__}: {err}"
        # A finding whose hook never started is not a finding about the hook.
        tag = ("PASS" if ok else "FAIL") if required else ("FINDING" if ok else "NO-EVIDENCE")
        print(f"{name:4} {tag:7} {line}", flush=True)
        if required and not ok:
            failed.append(name)
    for f in sorted(RUNS.glob("*/untranslated.json")) if RUNS.exists() else []:
        untranslated.update(json.loads(f.read_text()))
    print(f"untranslated request fields: {sorted(untranslated) or 'none'}")
    if failed:
        print(f"!! failed: {' '.join(failed)}; runs kept under {RUNS.relative_to(gtrace.REPO)}")
        return 1
    if not set(SCENARIOS) <= set(names):
        print("!! only some scenarios were run; nothing is claimed for the rest")
        return 1
    if not keep and RUNS.exists():
        shutil.rmtree(RUNS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
