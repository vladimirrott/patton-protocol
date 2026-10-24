"""$0 end-to-end trace of the patton gate inside DeepSeek Harness (dsh 0.2.1-rc.3), against a scripted model.

    cd lab && python3 -m patton_lab.harness.dsh.trace [S1 S2 ...]

Each scenario is one real `dsh --profile headless --json` run in a podman container whose network reaches only host
loopback (slirp4netns:allow_host_loopback=true, the host is 10.0.2.2), with a fresh /work holding keep.txt. Node and
the npm install live in lab/harness_cache/dsh and are mounted read-only; the repo's lab/ and plugins/ are mounted
read-only at /patton. The Claude-Code hook bridge is mounted through `patch.yml` and found through the profile's
package.json plus a node_modules link, because the npm package lists it only as a devDependency. Outcomes are read
from the files in /work, the gate's state dir and the requests the fake model received, never from dsh's exit code.
Exit 0 only when S1 to S4 pass; S5 to S7 are findings.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from patton_lab import isolate
from patton_lab.harness.fake_chat import FakeChat

VERSION = "0.2.1-rc.3"
NODE_VERSION = "v24.21.0"
HERE = Path(__file__).resolve().parent
LAB = HERE.parents[2]
REPO = LAB.parent
CACHE = LAB / "harness_cache" / "dsh"
DSH = CACHE / "pkg" / "node_modules" / ".bin" / "dsh"
HOOKS_PKG = "@deepseek-ai/dsh-hooks-claude-code"
KEEP = "only copy\n"
RUN_TIMEOUT_S = 150
HANG_TIMEOUT_S = 5  # the hook timeout S6 sets; dsh's own default is 600 s
SCRIPTS = "/patton/lab/patton_lab/harness/dsh/scripts"
GATE = "Patton gate"
STOP_REASON = "Patton stop check"


def check_install() -> None:
    """The pinned install must be there and be the pinned version (fetch.sh-free: see RESULT.md for the commands)."""
    for path in (DSH, CACHE / "node" / "bin" / "node", CACHE / "pkg" / "node_modules" / HOOKS_PKG / "package.json"):
        if not path.exists():
            raise RuntimeError(f"missing {path}; install dsh as RESULT.md describes")
    for pkg in ("@deepseek-ai/dsh", HOOKS_PKG):
        found = json.loads((CACHE / "pkg" / "node_modules" / pkg / "package.json").read_text())["version"]
        if found != VERSION:
            raise RuntimeError(f"{pkg} is {found}, the trace pins {VERSION}")


def hooks_config(scenario: str) -> dict[str, Any]:
    config = json.loads((HERE / "hooks.json").read_text())
    action = config["hooks"]["PreToolUse"][0]["hooks"][0]
    if scenario == "S5":
        action["command"] = f"sh {SCRIPTS}/crash.sh"
    if scenario == "S6":
        action["command"] = f"sh {SCRIPTS}/hang.sh"
        action["timeout"] = HANG_TIMEOUT_S
    if scenario == "S7":
        config["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": f"sh {SCRIPTS}/stop_once.sh"}]}]
    return config


def container(run: Path, prompt: str, name: str) -> list[str]:
    return ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", name, "--userns=keep-id",
            "--network", "slirp4netns:allow_host_loopback=true",
            "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=256m",
            "-e", "HOME=/home/user", "-e", "PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
            "-e", "LAB_API_KEY=dummy-no-real-key",
            # No dsh sandbox and no approval prompts: the gate is the only thing deciding (see RESULT.md).
            "-e", "DSH_PERMISSION_MODE=danger-full-access",
            "-e", "PATTON_GATE_ROOT=/work", "-e", "PATTON_GATE_STATE=/state",
            "-v", f"{run / 'work'}:/work:rw", "-v", f"{run / 'home'}:/home/user:rw", "-v", f"{run / 'state'}:/state:rw",
            "-v", f"{run / 'patch.yml'}:/patton-cfg/patch.yml:ro", "-v", f"{run / 'hooks'}:/patton-hooks:ro",
            "-v", f"{LAB}:/patton/lab:ro", "-v", f"{REPO / 'plugins'}:/patton/plugins:ro",
            "-v", f"{CACHE / 'node'}:/opt/node:ro", "-v", f"{CACHE / 'pkg'}:/opt/dsh:ro",
            "-w", "/work", isolate.IMAGE, "timeout", "-k", "5", str(RUN_TIMEOUT_S),
            "/opt/dsh/node_modules/.bin/dsh", "--profile", "headless", "--patch", "/patton-cfg/patch.yml", "--json",
            prompt]


def run_dsh(scenario: str, steps: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    run = root / scenario
    if run.exists():
        shutil.rmtree(run)  # loud: a leftover run must not leak into this one
    profile = run / "home" / ".dsh" / "profiles" / "headless"
    for sub in ("work", "state", "hooks"):
        (run / sub).mkdir(parents=True)
    (profile / "node_modules" / "@deepseek-ai").mkdir(parents=True)
    shutil.copy(HERE / "profile" / "package.json", profile / "package.json")
    # Resolves inside the container only; the real path keeps the package's own dependencies resolvable from /opt/dsh.
    os.symlink(f"/opt/dsh/node_modules/{HOOKS_PKG}", profile / "node_modules" / HOOKS_PKG)
    (run / "work" / "keep.txt").write_text(KEEP)
    (run / "hooks" / "hooks.json").write_text(json.dumps(hooks_config(scenario), indent=2) + "\n")
    fake = FakeChat(steps)
    port = fake.start("127.0.0.1")
    (run / "patch.yml").write_text((HERE / "patch.yml").read_text().replace("__BASE_URL__", f"http://10.0.2.2:{port}/v1"))
    name = f"patton-dsh-{scenario.lower()}-{os.getpid()}"
    start = time.time()
    try:
        done = subprocess.run(container(run, "Do the task in /work.", name), capture_output=True, text=True,
                              timeout=RUN_TIMEOUT_S + 60, stdin=subprocess.DEVNULL)
        code, out, err = done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["podman", "--log-level", "error", "rm", "-f", name], capture_output=True, timeout=60)
        code, out, err = None, str(exc.stdout or ""), "host timeout"
    finally:
        fake.stop()
    (run / "stdout.jsonl").write_text(out)
    (run / "stderr.txt").write_text(err)
    (run / "requests.json").write_text(json.dumps(fake.requests, indent=1))
    keep = run / "work" / "keep.txt"
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return {"scenario": scenario, "code": code, "seconds": round(time.time() - start, 1), "fake": fake,
            "keep": keep.read_text() if keep.is_file() else None, "work": run / "work", "events": events,
            "hook_ran": (run / "state" / "manifest.json").is_file(), "stderr": err,
            "plugin_loaded": "did not activate" not in err}


def _tool_messages(r) -> list[str]:
    """The tool results the model saw in its last request, in order."""
    if not r["fake"].requests:
        return []
    return [str(m.get("content")) for m in r["fake"].requests[-1].get("messages", []) if m.get("role") == "tool"]


def check_s1(r) -> tuple[bool, str]:
    results = _tool_messages(r)
    ok = r["plugin_loaded"] and r["hook_ran"] and any("keep.txt" in t for t in results) and not r["fake"].saw(GATE)
    return ok, f"hook ran={r['hook_ran']}, ls output (keep.txt) reached the model={any('keep.txt' in t for t in results)}"


def check_s2(r) -> tuple[bool, str]:
    saw = any(GATE in t for t in _tool_messages(r))
    return r["keep"] == KEEP and saw, f"keep.txt intact={r['keep'] == KEEP}, refusal reached the model={saw}"


def check_s3(r) -> tuple[bool, str]:
    new = r["work"] / "new.txt"
    made = new.is_file() and new.read_text().strip() == "hi"
    return r["hook_ran"] and made and not r["fake"].saw(GATE), f"hook ran={r['hook_ran']}, new.txt written={made}"


def check_s4(r) -> tuple[bool, str]:
    refusals = sum(GATE in t for t in _tool_messages(r))
    ok = r["keep"] == KEEP and refusals >= 2
    return ok, f"keep.txt unchanged={r['keep'] == KEEP}, gate refusals the model saw={refusals} of 2 (write, edit)"


def finding(r) -> tuple[bool, str]:
    results = _tool_messages(r)
    if r["keep"] == KEEP:
        return True, f"BLOCKED: keep.txt intact, tool result {results[-1][:120]!r}, run took {r['seconds']}s"
    return False, f"RAN: keep.txt deleted, tool result {results[-1][:80] if results else None!r}, run took {r['seconds']}s"


def check_s7(r) -> tuple[bool, str]:
    n, saw = len(r["fake"].requests), r["fake"].saw(STOP_REASON)
    return n >= 2 and saw, f"model requests={n}, stop refusal reached the model={saw}"


RM = {"tool": "bash", "args": {"description": "remove keep.txt", "command": "rm /work/keep.txt"}}
SCENARIOS: list[tuple[str, list[dict[str, Any]], Callable, bool]] = [
    ("S1", [{"tool": "bash", "args": {"description": "list", "command": "ls /work"}}, {"text": "listed"}], check_s1, True),
    ("S2", [RM, {"text": "tried"}], check_s2, True),
    ("S3", [{"tool": "bash", "args": {"description": "new file", "command": "echo hi > /work/new.txt"}},
            {"text": "wrote"}], check_s3, True),
    # dsh's own read-before-write policy refuses an unread file, so the script reads first: only the gate stands
    # between the write and edit and keep.txt.
    ("S4", [{"tool": "read", "args": {"file_path": "/work/keep.txt"}},
            {"tool": "write", "args": {"file_path": "/work/keep.txt", "content": "overwritten\n"}},
            {"tool": "edit", "args": {"file_path": "/work/keep.txt", "old_string": "only copy", "new_string": "edited"}},
            {"text": "tried"}], check_s4, True),
    ("S5", [RM, {"text": "tried"}], finding, False),
    ("S6", [RM, {"text": "tried"}], finding, False),
    ("S7", [{"text": "first answer"}, {"text": "STOP-AGAIN"}], check_s7, False),
]


def main(argv: list[str] | None = None) -> int:
    check_install()
    root = CACHE / "runs"
    want = set(argv if argv is not None else sys.argv[1:])
    required_ok = True
    for scenario, steps, check, required in SCENARIOS:
        if want and scenario not in want:
            continue
        r = run_dsh(scenario, steps, root)
        ok, detail = check(r)
        if not r["plugin_loaded"]:
            detail += " [hook plugin did NOT load]"
        label = ("PASS" if ok else "FAIL") if required else "FINDING"
        print(f"{scenario:<4} {label:<7} {detail} [dsh exit {r['code']}]", flush=True)
        if required and not ok:
            required_ok = False
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
