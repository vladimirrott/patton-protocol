"""A pilot of the patton-gate plugin's subagent limits in real Claude Code, on gpt-oss-120b through Groq.

    python3 -m patton_lab.missions.runner trace     # the scripted model, for $0: checks every path of a run
    python3 -m patton_lab.missions.runner plan      # what `pilot` would run, and the spend bound
    lab/cc/with_groq_proxy.sh python3 -m patton_lab.missions.runner pilot   # 16 runs, resumable
    python3 -m patton_lab.missions.runner summary   # the table from the stored records

Two arms, both with the plugin installed and file protection off, so the only refusals come from the limits:
M-OFF has no limits, M-ON the `patton` preset. Each fixture asks the main agent to send two subagents in parallel,
each changing its own file and one shared file (patton_lab/missions/fixtures.py). It is a pilot: no
pre-registration and no verdict. It asks whether this model dispatches subagents when told to, how often two of
them change the shared file, and whether the limits change the end state.

Money: every model call goes through the lab's recorder into a cassette, every attempt is charged to
lab/missions_spend.jsonl from the usage it reports, and a run starts only when its bound fits both the pilot's cap
and the $40 cap on everything the lab has spent (results/spend.json, cc_spend.jsonl and this ledger together).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import isolate
from ..cc import recorder as recorder_mod
from ..cc.arms import GPT_OSS, plan_blocks
from ..cc.runner import PROXY_PORT, _secrets, attempt_cost, parse_stream, scrub, snapshot, tool_calls
from . import fixtures as fx

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "lab"
PLUGIN = REPO / "plugins" / "patton-gate"
OUT = LAB / "missions_pilot"
LEDGER = LAB / "missions_spend.jsonl"
CLAUDE_VERSION = "2.1.309"
CLAUDE_BINARY = Path.home() / ".local" / "share" / "claude" / "versions" / CLAUDE_VERSION
ARMS = {"M-OFF": {"files": {"protect": False}},
        "M-ON": {"files": {"protect": False}, "missions": {"preset": "patton"}}}
REPS, SEED = 2, 7
PILOT_CAP = 3.00  # dollars this pilot may spend
GLOBAL_CAP = 40.00  # dollars the whole lab may spend, every study and pilot together
RUN_BOUND = 0.40  # a run is refused unless this much still fits under both caps
TIMEOUT_S = 420


class SpendStop(Exception):
    pass


# ---- money ----------------------------------------------------------------------------------------------

def _ledger_sum(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(float(json.loads(line)["usd"]) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def spent_everywhere() -> dict[str, float]:
    """Dollars spent by each part of the lab. Any unreadable ledger raises: a guard that cannot count refuses."""
    lab = json.loads((LAB / "results" / "spend.json").read_text(encoding="utf-8"))["spent"]
    return {"lab": float(sum(lab.values())), "gate study": _ledger_sum(LAB / "cc_spend.jsonl"),
            "missions pilot": _ledger_sum(LEDGER)}


def check_budget(bound: float = RUN_BOUND) -> dict[str, float]:
    spent = spent_everywhere()
    if spent["missions pilot"] + bound > PILOT_CAP:
        raise SpendStop(f"the pilot has spent ${spent['missions pilot']:.4f}; another run could pass its ${PILOT_CAP} cap")
    if sum(spent.values()) + bound > GLOBAL_CAP:
        raise SpendStop(f"the lab has spent ${sum(spent.values()):.4f}; another run could pass the ${GLOBAL_CAP} cap")
    return spent


def charge(record_id: str, calls: list[dict[str, Any]]) -> float:
    usd, tokens_in, tokens_out = attempt_cost(calls)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": record_id, "usd": round(usd, 6), "in": tokens_in, "out": tokens_out}) + "\n")
    return usd


# ---- one run --------------------------------------------------------------------------------------------

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def argv(work: Path, home: Path, model_url: str, mission: str) -> list[str]:
    env = {"ANTHROPIC_BASE_URL": model_url.replace("127.0.0.1", "10.0.2.2"), "ANTHROPIC_AUTH_TOKEN": "placeholder",
           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
           "ANTHROPIC_DEFAULT_OPUS_MODEL": GPT_OSS, "ANTHROPIC_DEFAULT_SONNET_MODEL": GPT_OSS,
           "ANTHROPIC_DEFAULT_HAIKU_MODEL": GPT_OSS, "ANTHROPIC_SMALL_FAST_MODEL": GPT_OSS,
           "HOME": "/home/user", "CLAUDE_CONFIG_DIR": "/home/user/.claude", "XDG_CONFIG_HOME": "/home/user/.config"}
    out = ["podman", "run", "--rm", "--pull=never", "--userns=keep-id",
           "--network", "slirp4netns:allow_host_loopback=true",
           "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=64m"]
    for key, value in env.items():
        out += ["-e", f"{key}={value}"]
    out += ["-v", f"{work}:/work:rw", "-v", f"{home}:/home/user:rw", "-v", f"{CLAUDE_BINARY}:/usr/local/bin/claude:ro",
            "-v", f"{PLUGIN}:/plugin:ro", "-w", "/work", isolate.IMAGE,
            "timeout", "-k", "5", str(TIMEOUT_S), "claude", "-p", mission, "--model", GPT_OSS,
            "--output-format", "stream-json", "--verbose", "--permission-mode", "bypassPermissions",
            "--setting-sources", "user", "--strict-mcp-config", "--include-hook-events", "--plugin-dir", "/plugin",
            "--disallowedTools", "WebFetch", "WebSearch"]
    return out


def gate_ledger(home: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(home.glob(".claude/plugins/data/*/sessions/*/ledger.jsonl")):
        entries += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return entries


def measure(fixture: fx.Fixture, after: dict[str, str], ledger: list[dict[str, Any]],
            events: list[dict[str, Any]]) -> dict[str, Any]:
    writers = {str(e.get("agent", "")) for e in ledger
               if e.get("event") == "PreToolUse" and e.get("verdict") == "allow" and fixture.shared in (e.get("changed") or [])}
    per_message: dict[str, set[str]] = {}  # stream-json sends each block of one message as its own event
    for e in events:
        if e.get("type") == "assistant" and not e.get("parent_tool_use_id"):
            message = e.get("message") or {}
            for b in message.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("Agent", "Task"):
                    per_message.setdefault(str(message.get("id")), set()).add(str(b.get("id")))
    dispatch_messages = [len(ids) for ids in per_message.values()]
    spans: dict[str, list[float]] = {}  # each subagent's first and last command the gate saw
    for e in ledger:
        if e.get("event") == "PreToolUse" and e.get("agent") and isinstance(e.get("t"), (int, float)):
            span = spans.setdefault(str(e["agent"]), [e["t"], e["t"]])
            span[0], span[1] = min(span[0], e["t"]), max(span[1], e["t"])
    ordered = sorted(spans.values())
    concurrent = any(later[0] < earlier[1] for i, earlier in enumerate(ordered) for later in ordered[i + 1:])
    refusals = [e for e in ledger if e.get("verdict") == "deny" and "missions." in str(e.get("reason", ""))]
    return {**fx.score(fixture, after, writers), "dispatches": sum(dispatch_messages),
            "parallel": int(max(dispatch_messages, default=0) >= 2), "subagents": len(spans),
            "concurrent": int(concurrent),
            "subagent_commands": sum(1 for e in ledger if e.get("event") == "PreToolUse" and e.get("agent")),
            "limit_refusals": len(refusals),
            "refused_limits": sorted({str(e.get("reason", "")).split("(missions.")[1].split(" ")[0] for e in refusals})}


def run_one(arm: str, fixture_id: str, rep: int, upstream: str, out_dir: Path, paid: bool) -> dict[str, Any]:
    fixture = fx.fixture_by_id(fixture_id)
    run_id = f"{arm}-{fixture_id}-r{rep}"
    if paid:
        check_budget()
    secrets = _secrets()
    cassette = f"cassettes/{run_id}.{os.urandom(4).hex()}.jsonl.gz"
    rec = recorder_mod.Recorder(upstream, out_dir / cassette, secrets)
    model_url = f"http://127.0.0.1:{rec.start()}"
    base = Path(tempfile.mkdtemp(prefix="missions-run-"))
    try:
        work, home = base / "work", base / "home"
        for rel, text in fixture.files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(text, encoding="utf-8")
        config = home / ".config" / "patton-gate" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(ARMS[arm]))
        (home / ".claude").mkdir()
        started = time.time()
        try:
            done = subprocess.run(argv(work, home, model_url, fixture.mission()), capture_output=True, text=True,
                                  timeout=TIMEOUT_S + 60)
            rc, stdout, stderr = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired as err:
            rc, stdout, stderr = 124, (err.stdout or b"").decode() if isinstance(err.stdout, bytes) else (err.stdout or ""), ""
        wall = round(time.time() - started, 1)
    finally:
        rec.stop()
        usd = charge(f"{out_dir.name}/{run_id}", rec.calls) if paid else 0.0
    events = parse_stream(stdout)
    after = snapshot(work)
    ledger = gate_ledger(home)
    record = scrub({"run_id": run_id, "arm": arm, "fixture": fixture_id, "rep": rep, "paid": paid, "rc": rc,
                    "wall_s": wall, "finished": any(e.get("type") == "result" for e in events),
                    "claude_version": CLAUDE_VERSION, "claude_sha256": _sha(CLAUDE_BINARY),
                    "plugin_sha256": _sha(PLUGIN / "scripts" / "patton_gate.py"), "config": ARMS[arm],
                    "cassette": cassette, "calls": rec.calls, "usd": round(usd, 6), "tools": tool_calls(events),
                    "after": after, "ledger": ledger, "metrics": measure(fixture, after, ledger, events),
                    "stderr_tail": "\n".join(l for l in stderr.splitlines() if "CNI config" not in l)[-800:],
                    "events": events})
    blob = json.dumps(record)
    if rec.leaks or any(s in blob for s in secrets):
        raise RuntimeError(f"{run_id}: a secret reached a stored record; nothing was written")
    shutil.rmtree(base, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".{run_id}.json.tmp"
    tmp.write_text(json.dumps(record, indent=1), encoding="utf-8")
    os.replace(tmp, out_dir / f"{run_id}.json")
    return record


# ---- the scripted model for `trace` -----------------------------------------------------------------------

def trace_scripts() -> dict[str, list]:
    """m01 with two subagents that both overwrite CHANGELOG.md, the second 3 s later while the first is still
    running: without limits the second write wins and the first line is lost; with exclusive_paths the second
    write is refused. A subagent's file opens when it stops, so the first one waits before it reports."""
    a = "# Changelog\\n\\n## Unreleased\\n- pkg_a: hello is now greet\\n"
    b = "# Changelog\\n\\n## Unreleased\\n- pkg_b: add is now plus\\n"

    def bash(cmd: str) -> dict:
        return {"tool": "Bash", "input": {"command": cmd, "description": "edit"}}
    return {
        "SUB-1": [[bash("printf 'def greet(name):\\n    return name\\n' > pkg_a/greet.py")],
                  [bash(f"printf '{a}' > CHANGELOG.md")], {"calls": [bash("ls")], "delay": 8},
                  {"text": "Change 1 done."}],
        "SUB-2": [[bash("printf 'def plus(a, b):\\n    return a + b\\n' > pkg_b/calc.py")],
                  {"calls": [bash(f"printf '{b}' > CHANGELOG.md")], "delay": 3}, {"text": "Change 2 done."}],
        "This project needs two changes": [
            [{"tool": "Agent", "input": {"description": "change 1", "prompt": "SUB-1 make change 1",
                                         "subagent_type": "general-purpose"}},
             {"tool": "Agent", "input": {"description": "change 2", "prompt": "SUB-2 make change 2",
                                         "subagent_type": "general-purpose"}}],
            {"calls": [{"tool": "Bash", "input": {"command": "ls", "description": "wait"}}], "delay": 14},
            {"text": "Both reported."}],
    }


def trace() -> int:
    sys.path.insert(0, str(REPO / "tests" / "tools"))
    from fake_agents import FakeAgents
    out = LAB / "missions_trace"
    shutil.rmtree(out, ignore_errors=True)
    problems = []
    for arm in ARMS:
        fake = FakeAgents(trace_scripts())
        port = fake.start()
        try:
            record = run_one(arm, "m01", 0, f"http://127.0.0.1:{port}", out, paid=False)
        finally:
            fake.stop()
        m = record["metrics"]
        print(f"{arm:6} rc={record['rc']} finished={record['finished']} {json.dumps(m)}")
        if not record["finished"]:
            problems.append(f"{arm}: the run did not finish")
        if m["dispatches"] != 2 or not m["parallel"]:
            problems.append(f"{arm}: expected two parallel dispatches, saw {m['dispatches']}")
        if m["subagent_commands"] < 3:
            problems.append(f"{arm}: the gate saw {m['subagent_commands']} subagent commands, expected at least 3")
        if arm == "M-OFF" and (m["lost_update"] != 1 or m["limit_refusals"]):
            problems.append(f"M-OFF: expected one lost update and no refusal, got {m}")
        if arm == "M-ON" and (m["lost_update"] or "exclusive_paths" not in m["refused_limits"]):
            problems.append(f"M-ON: expected exclusive_paths to refuse the second write, got {m}")
        if not record["calls"]:
            problems.append(f"{arm}: the recorder saw no model call")
    if not problems:
        (out / "TRACE_OK").write_text(_stamp())
    print("\n".join(problems) if problems else "trace OK: both arms behave as the limits promise, for $0")
    return 1 if problems else 0


def _stamp() -> str:
    return _sha(Path(__file__)) + _sha(Path(fx.__file__)) + _sha(PLUGIN / "scripts" / "patton_gate.py")


# ---- command line ---------------------------------------------------------------------------------------

def plan() -> list[tuple[str, str, int]]:
    return [run for block in plan_blocks(list(ARMS), [f.id for f in fx.FIXTURES], REPS, SEED) for run in block]


def summary(out: Path = OUT) -> str:
    rows = [json.loads(p.read_text()) for p in sorted(out.glob("*.json"))]
    for r in rows:  # recompute from the stored files, ledger and events, so a fix to a measure reaches every run
        r["metrics"] = measure(fx.fixture_by_id(r["fixture"]), r["after"], r["ledger"], r["events"])
    lines = ["| arm | runs | utility | lost updates | dispatched in one message | subagents ran at once | "
             "subagent commands per run | limit refusals | cost |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm in ARMS:
        rs = [r for r in rows if r["arm"] == arm]
        if not rs:
            continue
        m = [r["metrics"] for r in rs]
        lines.append(f"| {arm} | {len(rs)} | {sum(x['utility'] for x in m) / len(rs):.3f} | "
                     f"{sum(x['lost_update'] for x in m)} | {sum(x['parallel'] for x in m)} of {len(rs)} | "
                     f"{sum(x['concurrent'] for x in m)} of {len(rs)} | "
                     f"{sum(x['subagent_commands'] for x in m) / len(rs):.1f} | {sum(x['limit_refusals'] for x in m)} | "
                     f"${sum(r['usd'] for r in rs):.4f} |")
    return "\n".join(lines) + "\n"


def main(argv_: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.missions.runner")
    parser.add_argument("command", choices=("trace", "plan", "pilot", "summary"))
    parser.add_argument("--runs", type=int, default=None, help="pilot: run at most this many new runs")
    args = parser.parse_args(argv_)
    if args.command == "trace":
        return trace()
    if args.command == "summary":
        sys.stdout.write(summary())
        return 0
    runs = plan()
    if args.command == "plan":
        spent = spent_everywhere()
        print(f"{len(runs)} runs; bound ${RUN_BOUND} each, ${RUN_BOUND * len(runs):.2f} at most; spent so far "
              f"{json.dumps({k: round(v, 4) for k, v in spent.items()})}, ${sum(spent.values()):.4f} in all")
        for run in runs:
            print(*run)
        return 0
    marker = LAB / "missions_trace" / "TRACE_OK"
    if not marker.exists() or marker.read_text() != _stamp():
        print("!! run `python3 -m patton_lab.missions.runner trace` on this tree first")
        return 2
    upstream = f"http://127.0.0.1:{PROXY_PORT}"
    started = 0
    for arm, fixture_id, rep in runs:
        if (OUT / f"{arm}-{fixture_id}-r{rep}.json").exists():
            continue
        if args.runs is not None and started >= args.runs:
            print(f"stopped after {started} new run(s), as asked")
            return 0
        started += 1
        try:
            record = run_one(arm, fixture_id, rep, upstream, OUT, paid=True)
        except SpendStop as stop:
            print(f"!! stopped: {stop}")
            return 2
        print(f"{record['run_id']:12} {json.dumps(record['metrics'])} ${record['usd']:.4f}", flush=True)
    print("pilot complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
