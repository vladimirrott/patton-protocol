#!/usr/bin/env python3
"""End to end, for $0: the patton-gate plugin's discipline rules at Codex CLI's PreToolUse and Stop hooks.

    python3 tests/tools/gate_codex_turn_e2e.py

The plugin is installed and trusted as gate_codex_e2e.py does it, in a scratch HOME and CODEX_HOME, and the model is
the lab's scripted Responses endpoint. The user file turns file protection off and two discipline rules on. The
scripted model writes a test file, then says it is done twice. The checks: the test reminder reached the model's
next request, the first stop was refused with the gate's reason reaching the model, the second stop ended the run,
and the turn made exactly three model requests.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_codex_e2e import MODEL, codex, install, setup, shell  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SCRIPT = [shell("echo 'def test_x(): pass' > test_new.py"), {"text": "Done."}, {"text": "Done, really."}]


def main() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        home, codex_home, root, fake, env = setup(tmp, SCRIPT)
        config = home / ".config" / "patton-gate" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"files": {"protect": False},
                                      "discipline": {"reminders": True, "check_before_stop": True}}))
        try:
            if install(str(REPO), env, root, codex_home, problems):
                done = codex(["exec", "--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox",
                              "-m", MODEL, "Add a test file."], env, root)
                print(f"$ codex exec ...  -> rc {done.returncode}, {len(fake.requests)} model requests")
                texts = [json.dumps(r) for r in fake.requests]
                if not (root / "test_new.py").exists():
                    problems.append("the scripted write did not run")
                if not any("is a test" in t for t in texts[1:2]):
                    problems.append("the test reminder did not reach the model's next request")
                if not any("ran none of the project" in t for t in texts[2:]):
                    problems.append("the refused stop's reason did not reach the model")
                if len(fake.requests) != 3:
                    problems.append(f"expected 3 model requests, saw {len(fake.requests)}")
        finally:
            fake.stop()
    print("FAILURES:" if problems else "all checks passed")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
