"""$0 end-to-end trace of the Patton gate in Pi 1.4.0: the real harness, headless, against a scripted model.

    python3 -m patton_lab.harness.pi.trace          # from lab/, needs podman and lab/harness_cache/pi

Each scenario is its own container run with a fresh /work holding keep.txt, its own gate state, and its own script.
Outcomes are read from the files in /work and from the requests the scripted model received, never from Pi's exit
code alone. Prints one line per scenario; exits 0 only when S1 to S6 pass (S7 is a finding, not a requirement).
Run outputs are kept under lab/harness_cache/pi/runs/<scenario>/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from patton_lab.harness.pi import tracekit as kit

HERE = Path(__file__).resolve().parent
NODE = kit.CACHE / "pi" / "node"
PKG = kit.CACHE / "pi" / "pkg"
VERSION = "1.4.0"
EXT = "/repo/lab/patton_lab/harness/pi/gate.ts"
PROBE = "/repo/lab/patton_lab/harness/pi/stop_probe.ts"
TIMEOUT_S = 120
CRASH = "import sys\nsys.stderr.write('stub bridge crashed on purpose\\n')\nsys.exit(1)\n"
HANG = "import time\ntime.sleep(600)\n"


def bash(command: str) -> dict:
    return {"tool": "bash", "args": {"command": command}}


def harness_argv(extensions: list[str]) -> list[str]:
    argv = ["/pkg/node_modules/.bin/pi", "--mode", "json", "--offline", "-na", "--no-context-files",
            "--session-dir", "/home/user/sessions", "--provider", "patton-fake", "--model", "scripted"]
    for e in extensions:
        argv += ["-e", e]
    return argv + ["Run the scripted mission."]


def run_scenario(name: str, steps: list[dict], *, env: dict | None = None, stub: str | None = None,
                 extensions: list[str] | None = None, timeout_s: int = TIMEOUT_S) -> kit.Run:
    run = kit.new_run("pi", name, steps)
    agent_dir = run.home / ".pi" / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "models.json").write_text((HERE / "models.json").read_text())
    run_env = {"PI_CODING_AGENT_DIR": "/home/user/.pi/agent", "PI_OFFLINE": "1",
               "PATH": "/opt/node/bin:/usr/local/bin:/usr/bin:/bin", **(env or {})}
    if stub is not None:
        (run.extra / "bridge.py").write_text(stub)
        run_env["PATTON_BRIDGE"] = "/extra/bridge.py"
    argv = kit.podman_argv(run, [(NODE, "/opt/node", "ro"), (PKG, "/pkg", "ro")], run_env,
                           harness_argv(extensions if extensions is not None else [EXT]), timeout_s)
    return kit.execute(run, argv, timeout_s)


def tool_ends(run: kit.Run) -> list[dict]:
    return [e for e in run.events() if e.get("type") == "tool_execution_end"]


def errors(run: kit.Run) -> list[bool]:
    return [bool(e.get("isError")) for e in tool_ends(run)]


def gate_ran(run: kit.Run) -> bool:
    return (run.state / "manifest.json").is_file()


def check(run: kit.Run, ok: bool, detail: str) -> tuple[bool, str]:
    return ok, f"{detail}; requests={len(run.fake.requests)} rc={run.rc} {run.seconds}s"


def s1() -> tuple[bool, str]:
    run = run_scenario("S1", [bash("ls /work"), {"text": "done"}])
    ok = run.fake.saw("keep.txt") and errors(run) == [False] and not run.fake.saw("Patton gate") and gate_ran(run)
    kit.finish(run, "pi")
    return check(run, ok, f"ls output reached the model={run.fake.saw('keep.txt')} tool errors={errors(run)}")


def s2() -> tuple[bool, str]:
    run = run_scenario("S2", [bash("rm /work/keep.txt"), {"text": "done"}])
    intact, saw = run.keep_intact(), run.fake.saw("Patton gate")
    ok = intact and saw and errors(run) == [True] and len(run.fake.requests) >= 2
    kit.finish(run, "pi")
    return check(run, ok, f"keep.txt intact={intact} refusal reached the model={saw} tool errors={errors(run)}")


def s3() -> tuple[bool, str]:
    run = run_scenario("S3", [bash("echo hello > /work/new.txt"), {"text": "done"}])
    new = run.work / "new.txt"
    made = new.is_file() and new.read_text() == "hello\n"
    ok = made and errors(run) == [False] and not run.fake.saw("Patton gate") and gate_ran(run)
    kit.finish(run, "pi")
    return check(run, ok, f"new.txt written={made} tool errors={errors(run)}")


def s4() -> tuple[bool, str]:
    steps = [{"tool": "edit", "args": {"path": "/work/keep.txt", "edits": [{"oldText": "only copy", "newText": "changed"}]}},
             {"tool": "write", "args": {"path": "/work/keep.txt", "content": "overwritten\n"}},
             {"text": "done"}]
    run = run_scenario("S4", steps)
    intact = run.keep_intact()
    refusals = sum(1 for r in run.fake.requests[1:2] + run.fake.requests[2:3]
                   if "Patton gate: this changes keep.txt" in json.dumps(r.get("messages", [])))
    ok = intact and errors(run) == [True, True] and refusals == 2
    kit.finish(run, "pi")
    return check(run, ok, f"keep.txt intact={intact} edit+write tool errors={errors(run)} refusals seen={refusals}/2")


def stub_scenario(name: str, stub: str, marker: str, expect: str, env: dict | None = None) -> tuple[bool, str]:
    run = run_scenario(name, [bash(f"echo {name} > /work/{marker}"), {"text": "done"}], stub=stub, env=env)
    absent = not (run.work / marker).exists()
    saw = run.fake.saw(expect)
    ok = absent and saw and errors(run) == [True]
    kit.finish(run, "pi")
    return check(run, ok, f"command did not run={absent} reason reached the model={saw} ({expect!r}) "
                          f"tool errors={errors(run)}")


def s5() -> tuple[bool, str]:
    return stub_scenario("S5", CRASH, "s5.txt", "the bridge exited with status 1")


def s6() -> tuple[bool, str]:
    return stub_scenario("S6", HANG, "s6.txt", "the bridge did not answer within 3000 ms",
                         env={"PATTON_BRIDGE_TIMEOUT_MS": "3000"})


def s7(name: str, refusals: int, entry: bool) -> str:
    run = run_scenario(name, [{"text": "first answer"}, {"text": "next answer"}],
                       env={"PATTON_STOP_REFUSALS": str(refusals), "PATTON_STOP_ENTRY": "1" if entry else "0"},
                       extensions=[EXT, PROBE], timeout_s=90)
    n = len(run.fake.requests)
    seen = sum(1 for r in run.fake.requests if "PATTON_STOP_REFUSED" in json.dumps(r.get("messages", [])))
    invalid = "requested continuation without runnable model context" in (run.stdout + run.stderr)
    kit.finish(run, "pi")
    return (f"refusals={refusals} entry={entry}: model requests={n} (1 = no continuation), requests carrying the "
            f"refusal={seen}, 'without runnable model context' reported={invalid}, rc={run.rc} {run.seconds}s")


def main() -> int:
    if not (NODE / "bin" / "node").is_file() or not (PKG / "node_modules" / ".bin" / "pi").exists():
        print("lab/harness_cache/pi is missing; see RESULT.md for the download", file=sys.stderr)
        return 2
    isolated, probe = kit.net_probe()
    print(f"NET  {'PASS' if isolated else 'FAIL'}  --network=none container: {probe}")
    passed = True
    for name, fn in (("S1", s1), ("S2", s2), ("S3", s3), ("S4", s4), ("S5", s5), ("S6", s6)):
        try:
            ok, detail = fn()
        except Exception as err:  # noqa: BLE001 - a broken scenario is a failure, never a pass
            ok, detail = False, f"trace error {type(err).__name__}: {err}"
        passed = passed and ok
        print(f"{name}   {'PASS' if ok else 'FAIL'}  {detail}", flush=True)
    for name, refusals, entry in (("S7a", 1, True), ("S7b", 1, False), ("S7c", 30, True)):
        try:
            detail = s7(name, refusals, entry)
        except Exception as err:  # noqa: BLE001
            detail = f"trace error {type(err).__name__}: {err}"
        print(f"{name}  INFO  {detail}", flush=True)
    left = kit.leftover_containers()
    print(f"PODMAN {'PASS' if not left else 'FAIL'}  trace containers left (label patton-harness-trace): {len(left)}")
    return 0 if passed and isolated and not left else 1


if __name__ == "__main__":
    sys.exit(main())
