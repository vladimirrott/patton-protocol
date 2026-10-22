#!/usr/bin/env python3
"""The person's view: interactive Claude Code asks before deleting a file that existed, and honours the answer.

    python3 tests/tools/gate_plugin_tui.py --claude claude --plugin-dir plugins/patton-gate [--home DIR]
    python3 tests/tools/gate_plugin_tui.py --claude claude --installed

Runs Claude Code in a tmux session against a scripted model that asks to delete notes.txt, waits for the
permission prompt, checks it carries the gate's reason, answers it, and checks what happened to the file. Two
runs: one answered No, one answered Yes. Needs tmux. No key, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lab"))
from patton_lab.cc.fake_upstream import FakeUpstream  # noqa: E402


SOCKET = f"patton-tui-{os.getpid()}"  # a private tmux server, so the person's own tmux and its options stay untouched


def tmux(*args: str) -> str:
    return subprocess.run(["tmux", "-L", SOCKET, *args], capture_output=True, text=True).stdout


def new_session(name: str, root: Path, command: list[str]) -> None:
    # remain-on-exit keeps the pane, so a Claude Code that exits at startup leaves its last screen to read.
    tmux("new-session", "-d", "-s", name, "-x", "160", "-y", "50", "-c", str(root), *command,
         ";", "set-option", "-g", "remain-on-exit", "on")


def stop(name: str, seconds: float = 15) -> None:
    """Kill the session and wait for Claude Code to exit, so it is done writing before its scratch home goes."""
    pid = tmux("display-message", "-p", "-t", name, "#{pane_pid}").strip()
    tmux("kill-session", "-t", name)
    if not pid.isdigit() or int(pid) <= 1:
        return
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return
        time.sleep(0.2)


def exited(name: str) -> bool:
    return tmux("display-message", "-p", "-t", name, "#{pane_dead}").strip() == "1"


def wait_for(session: str, needles: tuple[str, ...], seconds: float) -> str:
    deadline = time.monotonic() + seconds
    screen = ""
    while time.monotonic() < deadline:
        screen = tmux("capture-pane", "-p", "-J", "-t", session, "-S", "-200")
        if any(n in screen for n in needles):
            return screen
        time.sleep(0.5)
    return screen


def dismiss_notices(name: str, seconds: float = 20) -> None:
    """Claude Code shows one-off notices ("Enter to continue") that wait for a key; acknowledge them."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        screen = tmux("capture-pane", "-p", "-J", "-t", name)
        if "Enter to continue" in screen and "Do you want" not in screen:
            tmux("send-keys", "-t", name, "Enter")
            time.sleep(1)
            continue
        if "for shortcuts" in screen or "? for" in screen:
            return
        time.sleep(0.5)


COMMAND = "python3 -c 'print(6 * 7)'"
ALLOW_RULE = "Bash(python3 -c:*)"


def opaque_run(claude: str, plugin_dir: str | None, home: Path | None, allow: bool = False,
               tag: str = "opaque") -> dict:
    """The same unreadable command twice: one question, answered Yes, then the repeat runs without asking.

    With allow=True the user's settings allow the command, which must not silence the gate's question.
    """
    steps = [{"tool": "Bash", "input": {"command": COMMAND, "description": "Compute"}},
             {"tool": "Bash", "input": {"command": COMMAND, "description": "Compute again"}},
             {"text": "Finished."}]
    return session(claude, plugin_dir, home, steps, tag, allow)


def session(claude: str, plugin_dir: str | None, home: Path | None, steps: list, tag: str,
            allow: bool = False) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        root.mkdir()
        (root / "notes.txt").write_text("keep me\n")
        fake = FakeUpstream(steps)
        port = fake.start()
        run_home = prepare_home(home or Path(tmp) / "home", root)
        settings = run_home / ".claude" / "settings.json"
        before = settings.read_bytes() if settings.exists() else None
        if allow:  # merged into the user's settings and put back afterwards, so an installed plugin stays enabled
            current = json.loads(before) if before else {}
            current.setdefault("permissions", {}).setdefault("allow", []).append(ALLOW_RULE)
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_text(json.dumps(current))
        name = f"patton-tui-{os.getpid()}-{tag}"
        start(name, claude, plugin_dir, run_home, root, port)
        questions, reason, screen = 0, False, ""
        try:
            wait_for(name, ("for shortcuts", "? for", "Enter to continue"), 60)
            dismiss_notices(name)
            tmux("send-keys", "-t", name, "Compute twice.", "Enter")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                screen = wait_for(name, ("Do you want", "Finished.", "Enter to continue"), 30)
                if "Enter to continue" in screen and "Do you want" not in screen:
                    dismiss_notices(name, 5)
                    continue
                if "Do you want" in screen.split("Finished.")[-1] and "Finished." not in screen:
                    questions += 1
                    reason = reason or "cannot tell which files" in screen
                    tmux("send-keys", "-t", name, "Enter")
                    time.sleep(1.5)
                    continue
                break
            dead = exited(name)
            if dead:
                screen = tmux("capture-pane", "-p", "-J", "-t", name, "-S", "-200")
            return {"questions": questions, "reason_on_screen": reason, "finished": "Finished." in screen,
                    "exited": dead, "screen": screen}
        finally:
            stop(name)
            fake.stop()
            if allow:
                if before is None:
                    settings.unlink()
                else:
                    settings.write_bytes(before)


def prepare_home(run_home: Path, root: Path) -> Path:
    run_home.mkdir(parents=True, exist_ok=True)
    state = run_home / ".claude.json"
    config = json.loads(state.read_text()) if state.exists() else {}
    config.update(hasCompletedOnboarding=True, theme="dark")
    config.setdefault("projects", {})[str(root)] = {"hasTrustDialogAccepted": True, "allowedTools": []}
    state.write_text(json.dumps(config))
    return run_home


def start(name: str, claude: str, plugin_dir: str | None, run_home: Path, root: Path, port: int) -> None:
    env = {"HOME": str(run_home), "PATH": os.environ["PATH"], "TERM": "xterm-256color", "LANG": "C.UTF-8",
           "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_AUTH_TOKEN": "placeholder",
           "ANTHROPIC_DEFAULT_SONNET_MODEL": "fake", "ANTHROPIC_DEFAULT_OPUS_MODEL": "fake",
           "ANTHROPIC_SMALL_FAST_MODEL": "fake", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    argv = [claude, "--model", "fake"] + (["--plugin-dir", plugin_dir] if plugin_dir else [])
    new_session(name, root, ["env", "-i", *[f"{k}={v}" for k, v in env.items()], *argv])


def one_run(claude: str, plugin_dir: str | None, home: Path | None, answer: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        root.mkdir()
        (root / "notes.txt").write_text("keep me\n")
        fake = FakeUpstream([{"tool": "Bash", "input": {"command": "rm notes.txt", "description": "Remove notes.txt"}},
                             {"text": "Finished."}])
        port = fake.start()
        run_home = prepare_home(home or Path(tmp) / "home", root)
        session = f"patton-tui-{os.getpid()}-{answer}"
        start(session, claude, plugin_dir, run_home, root, port)
        try:
            screen = wait_for(session, ("for shortcuts", "? for", "Enter to continue"), 60)
            dismiss_notices(session)
            tmux("send-keys", "-t", session, "Tidy this project.", "Enter")
            prompt = wait_for(session, ("Patton gate", "Do you want"), 60)
            seen_reason = "Patton gate" in prompt and "notes.txt" in prompt
            if answer == "yes":
                tmux("send-keys", "-t", session, "Enter")  # the first option is Yes
            else:
                tmux("send-keys", "-t", session, "Escape")  # Escape declines the call
            final = wait_for(session, ("Finished.",), 60)
            return {"answer": answer, "prompt_shown": "Do you want" in prompt or "Patton gate" in prompt,
                    "reason_on_screen": seen_reason, "file_kept": (root / "notes.txt").exists(),
                    "finished": "Finished." in final, "prompt_excerpt": prompt[-1400:]}
        finally:
            stop(session)
            fake.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--plugin-dir")
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--home")
    args = parser.parse_args()
    if not shutil.which("tmux"):
        print("!! tmux is needed")
        return 2
    if not args.installed and not args.plugin_dir:
        parser.error("give --plugin-dir or --installed")
    if args.plugin_dir:
        # Claude Code runs in a scratch project, so a relative path would load no plugin at all.
        args.plugin_dir = str(Path(args.plugin_dir).resolve())
        if not (Path(args.plugin_dir) / ".claude-plugin" / "plugin.json").is_file():
            parser.error(f"{args.plugin_dir} holds no .claude-plugin/plugin.json")
    home =Path(args.home) if args.home else (Path.home() if args.installed else None)
    plugin = None if args.installed else args.plugin_dir
    results = [one_run(args.claude, plugin, home, a) for a in ("no", "yes")]
    opaque = opaque_run(args.claude, plugin, home)
    allowed = opaque_run(args.claude, plugin, home, allow=True, tag="allowed")
    # Control: without the plugin the same rule must run the command unasked, or the rule proves nothing.
    control = None if args.installed else opaque_run(args.claude, None, home, allow=True, tag="control")
    failures = []
    for r in results:
        print(f"== answered {r['answer']}: prompt shown {r['prompt_shown']}, gate reason on screen "
              f"{r['reason_on_screen']}, notes.txt kept {r['file_kept']}, mission finished {r['finished']}")
        if not (r["prompt_shown"] and r["reason_on_screen"]):
            failures.append(f"{r['answer']}: the prompt with the gate's reason did not appear")
            print(r["prompt_excerpt"])
        if r["answer"] == "no" and not r["file_kept"]:
            failures.append("answered no, and notes.txt was deleted anyway")
        if r["answer"] == "yes" and r["file_kept"]:
            failures.append("answered yes, and notes.txt is still there")
    print(f"== an unreadable command twice: questions {opaque['questions']}, gate reason on screen "
          f"{opaque['reason_on_screen']}, mission finished {opaque['finished']}")
    if opaque["questions"] != 1 or not opaque["reason_on_screen"] or not opaque["finished"]:
        failures.append("the unreadable command was not asked about exactly once, with the gate's reason")
        print(opaque["screen"][-1400:])
    print(f"== the same command, allowed by {ALLOW_RULE} in the user's settings: questions {allowed['questions']}, "
          f"gate reason on screen {allowed['reason_on_screen']}, mission finished {allowed['finished']}")
    if allowed["questions"] != 1 or not allowed["reason_on_screen"] or not allowed["finished"]:
        failures.append("Claude Code exited before the allow-rule check could run" if allowed["exited"]
                        else "an allow rule in the user's settings changed the gate's question")
        print(allowed["screen"][-2400:])
    if control is not None:
        print(f"== control, the same rule without the plugin: questions {control['questions']}, "
              f"mission finished {control['finished']}")
        if control["questions"] != 0 or not control["finished"]:
            failures.append("without the plugin the allow rule did not let the command run unasked, "
                            "so the allow-rule check proves nothing")
            print(control["screen"][-1400:])
    print("FAILURES:" if failures else "all checks passed")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        tmux("kill-server")  # the private server and any pane it kept
        # kill-server leaves the socket file behind
        (Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}" / SOCKET).unlink(missing_ok=True)
    sys.exit(code)
