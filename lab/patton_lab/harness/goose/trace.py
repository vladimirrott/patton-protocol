"""$0 end-to-end trace of the patton gate inside goose v1.56.0, against a scripted model.

    cd lab && python3 -m patton_lab.harness.goose.trace

Each scenario is one real `goose run` in a podman container whose network reaches only host loopback
(slirp4netns:allow_host_loopback=true, the host is 10.0.2.2), with a fresh /work holding keep.txt, the gate plugin
mounted read-only at ~/.agents/plugins/patton-gate, and the repo's lab/ and plugins/ mounted read-only at /patton.
Outcomes are read from the files in /work, the gate's state dir and the requests the fake model received, never
from goose's exit code. Exit 0 only when S1 to S4 pass; S5 to S7 are findings.
"""

from __future__ import annotations

import hashlib
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

VERSION = "1.56.0"
TARBALL = "goose-x86_64-unknown-linux-gnu.tar.bz2"
URL = f"https://github.com/aaif-goose/goose/releases/download/v{VERSION}/{TARBALL}"
SHA256 = "e29544421a6e698aab0fe4becda96096218ff36abe85b83785519d41c555c714"  # the release asset's published digest

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[2]
REPO = LAB.parent
CACHE = LAB / "harness_cache" / "goose"
BINARY = CACHE / "bin" / "goose"
PLUGIN = HERE / "plugin" / "patton-gate"
KEEP = "only copy\n"
RUN_TIMEOUT_S = 120
HANG_TIMEOUT_S = 5  # the hook timeout S6 sets, so the hang is cut long before the run's own limit
DENIED = "Tool call denied by policy hook"
FAILED = "could not complete"
STOP_REASON = "Patton stop check"


def fetch() -> None:
    """Download and unpack the pinned release once, refusing any tarball whose sha256 is not the published one."""
    if BINARY.is_file():
        return
    dl = CACHE / "dl"
    dl.mkdir(parents=True, exist_ok=True)
    tarball = dl / TARBALL
    if not tarball.is_file():
        subprocess.run(["curl", "-fsSL", "-o", str(tarball), URL], check=True, timeout=900)
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest != SHA256:
        tarball.unlink()
        raise RuntimeError(f"{TARBALL} has sha256 {digest}, the release publishes {SHA256}")
    (CACHE / "bin").mkdir(exist_ok=True)
    subprocess.run(["tar", "-xjf", str(tarball), "-C", str(CACHE / "bin")], check=True, timeout=300)


def hooks_config(scenario: str) -> dict[str, Any]:
    """The shipped hooks.json, or the variant a failure or stop scenario needs."""
    config = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    action = config["hooks"]["PreToolUse"][0]["hooks"][0]
    if scenario.startswith("S5"):
        action["command"] = "sh ${PLUGIN_ROOT}/scripts/crash.sh"
    if scenario.startswith("S6"):
        action["command"] = "sh ${PLUGIN_ROOT}/scripts/hang.sh"
        action["timeout"] = HANG_TIMEOUT_S
    if scenario.endswith("default"):
        del action["on_failure"]  # goose's own default: a failed hook lets the call through
    if scenario == "S7":
        config["hooks"]["Stop"] = [{"hooks": [{"type": "command", "command": "sh ${PLUGIN_ROOT}/scripts/stop_once.sh"}]}]
    return config


def container(run: Path, port: int, prompt: str, name: str) -> list[str]:
    return ["podman", "--log-level", "error", "run", "--rm", "--pull=never", "--name", name, "--userns=keep-id",
            "--network", "slirp4netns:allow_host_loopback=true",
            "--cap-drop=all", "--security-opt=no-new-privileges", "--read-only", "--tmpfs", "/tmp:size=64m",
            "-e", "HOME=/home/user", "-e", "GOOSE_PROVIDER=openai", "-e", "GOOSE_MODEL=scripted",
            "-e", f"OPENAI_HOST=http://10.0.2.2:{port}", "-e", "OPENAI_API_KEY=dummy-no-real-key",
            "-e", "GOOSE_DISABLE_KEYRING=1", "-e", "GOOSE_MODE=auto",
            "-e", "PATTON_GATE_ROOT=/work", "-e", "PATTON_GATE_STATE=/state",
            "-v", f"{run / 'work'}:/work:rw", "-v", f"{run / 'home'}:/home/user:rw", "-v", f"{run / 'state'}:/state:rw",
            "-v", f"{run / 'plugin'}:/home/user/.agents/plugins/patton-gate:ro",
            "-v", f"{LAB}:/patton/lab:ro", "-v", f"{REPO / 'plugins'}:/patton/plugins:ro",
            "-v", f"{BINARY.parent}:/opt/goose:ro",
            "-w", "/work", isolate.IMAGE, "timeout", "-k", "5", str(RUN_TIMEOUT_S),
            # --name marks the session user-named, so goose sends no title request to the scripted model.
            "/opt/goose/goose", "run", "--name", "patton-trace", "--output-format", "stream-json",
            "--max-turns", "8", "-t", prompt]


def run_goose(scenario: str, steps: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    run = root / scenario
    if run.exists():
        shutil.rmtree(run)  # loud: a leftover run must not leak into this one
    # The mount point inside HOME is made here, so podman does not create it as a subordinate uid we cannot remove.
    for sub in ("work", "home/.agents/plugins/patton-gate", "state", "plugin"):
        (run / sub).mkdir(parents=True)
    (run / "work" / "keep.txt").write_text(KEEP)
    shutil.copytree(PLUGIN, run / "plugin", dirs_exist_ok=True)
    (run / "plugin" / "hooks" / "hooks.json").write_text(json.dumps(hooks_config(scenario), indent=2) + "\n")
    fake = FakeChat(steps)
    port = fake.start("127.0.0.1")
    name = f"patton-goose-{scenario.lower()}-{os.getpid()}"
    start = time.time()
    try:
        done = subprocess.run(container(run, port, "Do the task in /work.", name), capture_output=True, text=True,
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
    return {"scenario": scenario, "code": code, "seconds": round(time.time() - start, 1), "fake": fake,
            "keep": keep.read_text() if keep.is_file() else None, "work": run / "work",
            "hook_ran": (run / "state" / "manifest.json").is_file(), "stdout": out}


def _requests_after_first(fake: FakeChat) -> str:
    return json.dumps([r.get("messages", []) for r in fake.requests[1:]])


def check_s1(r) -> tuple[bool, str]:
    later = _requests_after_first(r["fake"])
    ok = r["hook_ran"] and "keep.txt" in later and DENIED not in later
    return ok, f"hook ran={r['hook_ran']}, ls output (keep.txt) reached the model={'keep.txt' in later}"


def check_s2(r) -> tuple[bool, str]:
    saw = r["fake"].saw("Patton gate")
    ok = r["keep"] == KEEP and saw and r["fake"].saw(DENIED)
    return ok, f"keep.txt intact={r['keep'] == KEEP}, refusal reached the model={saw}"


def check_s3(r) -> tuple[bool, str]:
    new = r["work"] / "new.txt"
    made = new.is_file() and new.read_text().strip() == "hi"
    return r["hook_ran"] and made and not r["fake"].saw(DENIED), f"hook ran={r['hook_ran']}, new.txt written={made}"


def check_s4(r) -> tuple[bool, str]:
    last = json.dumps(r["fake"].requests[-1].get("messages", [])) if r["fake"].requests else ""
    refusals = last.count(DENIED + " `patton-gate`: Patton gate")
    ok = r["keep"] == KEEP and refusals >= 2
    return ok, f"keep.txt unchanged={r['keep'] == KEEP}, gate refusals seen by the model={refusals} (write, edit)"


def finding(r) -> tuple[bool, str]:
    later = _requests_after_first(r["fake"])
    if r["keep"] == KEEP:
        how = "failure message" if FAILED in later else ("policy denial" if DENIED in later else "no message")
        return True, f"BLOCKED: keep.txt intact ({how} reached the model), run took {r['seconds']}s"
    return False, f"RAN: keep.txt deleted, the rm went through, run took {r['seconds']}s"


def check_s7(r) -> tuple[bool, str]:
    n, saw = len(r["fake"].requests), r["fake"].saw(STOP_REASON)
    ok = n >= 2 and saw
    return ok, f"model requests={n}, stop refusal reached the model={saw}"


RM = {"tool": "shell", "args": {"command": "rm /work/keep.txt"}}
SCENARIOS: list[tuple[str, list[dict[str, Any]], Callable, bool]] = [
    ("S1", [{"tool": "shell", "args": {"command": "ls /work"}}, {"text": "listed"}], check_s1, True),
    ("S2", [RM, {"text": "tried"}], check_s2, True),
    ("S3", [{"tool": "shell", "args": {"command": "echo hi > /work/new.txt"}}, {"text": "wrote"}], check_s3, True),
    ("S4", [{"tool": "write", "args": {"path": "/work/keep.txt", "content": "overwritten\n"}},
            {"tool": "edit", "args": {"path": "/work/keep.txt", "before": "only copy", "after": "edited"}},
            {"text": "tried"}], check_s4, True),
    ("S5", [RM, {"text": "tried"}], finding, False),
    ("S5-default", [RM, {"text": "tried"}], finding, False),
    ("S6", [RM, {"text": "tried"}], finding, False),
    ("S6-default", [RM, {"text": "tried"}], finding, False),
    ("S7", [{"text": "first answer"}, {"text": "STOP-AGAIN"}], check_s7, False),
]


def main(argv: list[str] | None = None) -> int:
    fetch()
    root = CACHE / "runs"
    want = set(argv if argv is not None else sys.argv[1:])
    required_ok = True
    for scenario, steps, check, required in SCENARIOS:
        if want and scenario not in want:
            continue
        r = run_goose(scenario, steps, root)
        ok, detail = check(r)
        label = ("PASS" if ok else "FAIL") if required else "FINDING"
        print(f"{scenario:<11} {label:<7} {detail} [goose exit {r['code']}]", flush=True)
        if required and not ok:
            required_ok = False
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
