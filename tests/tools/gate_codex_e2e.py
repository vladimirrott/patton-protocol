#!/usr/bin/env python3
"""End to end, for $0: install the patton-gate plugin into real Codex CLI the way a person does, then drive it.

    python3 tests/tools/gate_codex_e2e.py                      # the marketplace in this checkout
    python3 tests/tools/gate_codex_e2e.py --source vladimirrott/patton-protocol   # the published one

Every codex call runs with a scratch HOME and CODEX_HOME, so the person's own ~/.codex and login are never read.
The model is the lab's scripted Responses endpoint (lab/patton_lab/codex/fake_upstream.py) on 127.0.0.1, with a
placeholder key. The script:

1. adds the marketplace, adds the plugin, and trusts its hooks with `patton_gate.py codex-trust`;
2. runs `codex exec`: the model lists the project, deletes a file that existed, runs `pwd` with a workdir the hook
   cannot see, adds a file by patch, and changes the protected file by patch;
3. reads the held ids and runs `codex exec resume --last "approve <id> approve <id>"`, after which the model makes
   the two held calls again.

It checks that both held calls were stopped the first time and ran the second, that the workdir was pinned, and
that the new file needed no approval.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lab"))
from patton_lab.codex.fake_upstream import FakeUpstream  # noqa: E402

MODEL = "gpt-6.1-sol"
WRAP = 'async function t(f){ try { return JSON.stringify(await f()); } catch (e) { return "refused: " + e; } }\n'


def cell(js: str) -> dict:
    return {"exec": WRAP + f"text(await t(() => {js}));"}


def shell(cmd: str, workdir: str | None = None) -> dict:
    args = {"cmd": cmd, **({"workdir": workdir} if workdir else {})}
    return cell(f"tools.exec_command({json.dumps(args)})")


def patch(body: str) -> dict:
    return cell(f"tools.apply_patch({json.dumps(body)})")


ADD = "*** Begin Patch\n*** Add File: NEW.md\n+new\n*** End Patch\n"
CHANGE = "*** Begin Patch\n*** Update File: notes.txt\n@@\n-keep me\n+changed by patch\n*** End Patch\n"
SCRIPT = [shell("ls -A"), shell("rm notes.txt"), shell("pwd", "/tmp"), patch(ADD), patch(CHANGE),
          {"text": "Two calls are held."},
          patch(CHANGE), shell("cat notes.txt"), shell("rm notes.txt"), {"text": "Done."}]


def codex(argv: list[str], env: dict, cwd: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["codex", *argv], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)  # codex exec reads stdin when it is not a terminal
    except subprocess.TimeoutExpired as err:  # report what it printed instead of losing it
        out = err.stdout.decode() if isinstance(err.stdout, bytes) else (err.stdout or "")
        errs = err.stderr.decode() if isinstance(err.stderr, bytes) else (err.stderr or "")
        return subprocess.CompletedProcess(err.cmd, 124, out, errs + f"\n[timed out after {timeout} s]")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(REPO), help="marketplace source: a path or owner/repo")
    args = parser.parse_args()
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        home, codex_home, root = Path(tmp) / "home", Path(tmp) / "home" / ".codex", Path(tmp) / "project"
        codex_home.mkdir(parents=True)
        root.mkdir()
        (root / "notes.txt").write_text("keep me\n")
        catalog = json.loads((REPO / "lab" / "codex" / "catalog" / f"{MODEL}.json").read_text())
        (codex_home / "catalog.json").write_text(json.dumps({"models": [catalog]}))
        fake = FakeUpstream(SCRIPT)
        port = fake.start()
        (codex_home / "config.toml").write_text(
            f'model = "{MODEL}"\nmodel_reasoning_effort = "medium"\nmodel_provider = "fake"\n'
            f'model_catalog_json = "{codex_home / "catalog.json"}"\nweb_search = "disabled"\n\n'
            "[agents]\nenabled = false\n\n"
            f'[model_providers.fake]\nname = "fake"\nbase_url = "http://127.0.0.1:{port}/backend-api/codex"\n'
            'wire_api = "responses"\nenv_key = "PATTON_PLACEHOLDER_KEY"\nsupports_websockets = false\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI", "CODEX", "PATTON_GATE"))}
        env.update(HOME=str(home), CODEX_HOME=str(codex_home), PATTON_PLACEHOLDER_KEY="placeholder",
                   XDG_CONFIG_HOME=str(home / ".config"), XDG_STATE_HOME=str(home / ".local" / "state"))
        try:
            for argv in (["plugin", "marketplace", "add", args.source], ["plugin", "add", "patton-gate@patton-protocol"]):
                done = codex(argv, env, root)
                print(f"$ codex {' '.join(argv)}  -> rc {done.returncode}")
                if done.returncode != 0:
                    problems.append(f"codex {' '.join(argv)} failed: {(done.stdout + done.stderr)[-400:]}")
                    raise SystemExit
            scripts = sorted(glob.glob(str(codex_home / "plugins" / "cache" / "patton-protocol" / "patton-gate" / "*"
                                           / "scripts" / "patton_gate.py")))
            if not scripts:
                problems.append("the plugin is not in the Codex plugin cache")
                raise SystemExit
            trust = subprocess.run([sys.executable, scripts[-1], "codex-trust"], cwd=root, env=env, capture_output=True,
                                   text=True, timeout=120)
            print(f"$ python3 .../patton_gate.py codex-trust  -> rc {trust.returncode}\n{trust.stdout}")
            if trust.returncode != 0:
                problems.append("codex-trust failed")
                raise SystemExit
            base = ["exec", "--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "-m", MODEL]
            first = codex(base + ["Tidy this project."], env, root)
            print(f"$ codex exec ...  -> rc {first.returncode}, {len(fake.requests)} model requests")
            if first.returncode != 0:
                print("stdout tail:", first.stdout[-1500:], "\nstderr tail:", first.stderr[-1500:])
            if not (root / "notes.txt").exists() or (root / "notes.txt").read_text() != "keep me\n":
                problems.append("first run: notes.txt was deleted or changed before anyone approved it")
            if not (root / "NEW.md").exists():
                problems.append("first run: the patch adding NEW.md did not run")
            pwd_out = [str(item.get("output")) for r in fake.requests for item in r.get("input") or []
                       if isinstance(item, dict) and item.get("call_id") == "call_2" and "output" in item]
            print("pwd with workdir /tmp printed:", [o[:700] for o in pwd_out[:1]])
            if not pwd_out or f'"output":"{root}\\\\n"' not in pwd_out[0]:
                problems.append("first run: pwd with workdir /tmp did not run in the project directory")
            holds = sorted(glob.glob(str(codex_home / "plugins" / "data" / "*" / "sessions" / "*" / "holds.json")))
            held = list(json.loads(Path(holds[-1]).read_text())) if holds else []
            print(f"held ids: {held}")
            if len(held) != 2:
                problems.append(f"first run: expected 2 held calls, found {len(held)} ({holds})")
                raise SystemExit
            second = codex(["exec", "resume", "--last", "--json", "--skip-git-repo-check",
                            "--dangerously-bypass-approvals-and-sandbox", "-m", MODEL,
                            " ".join(f"approve {h}" for h in held)], env, root)
            print(f"$ codex exec resume --last 'approve ...'  -> rc {second.returncode}")
            later = "".join(json.dumps(r) for r in fake.requests[6:])
            if "Patton gate: the person approved" not in later:
                problems.append("second run: the model was not told about the approval")
            if "changed by patch" not in later:
                problems.append("second run: the approved patch did not change notes.txt")
            if (root / "notes.txt").exists():
                problems.append("second run: the approved rm did not delete notes.txt")
            ledgers = glob.glob(str(codex_home / "plugins" / "data" / "*" / "sessions" / "*" / "ledger.jsonl"))
            verdicts = [json.loads(line).get("verdict") for path in ledgers for line in Path(path).read_text().splitlines()]
            print(f"ledger verdicts: {verdicts}")
            for text in (first.stderr, second.stderr):
                if re.search(r"untrusted|hook.*(skipp|not trusted)", text, re.I):
                    problems.append("codex reported the hooks as untrusted: " + text[-300:])
        except SystemExit:
            pass
        finally:
            fake.stop()
    print("FAILURES:" if problems else "all checks passed")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
