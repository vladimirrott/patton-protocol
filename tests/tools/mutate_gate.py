#!/usr/bin/env python3
"""Mutation proof for the gate: delete one rule at a time and require its tests to notice.

Each mutant is (name, group, old, new): `old` must occur exactly once in gate/patton_gate.py, it is
replaced by `new` in a scratch copy, and tests/test_gate_corpus.py runs against that copy. A mutant is
KILLED when the run fails and, for a corpus mutant, the failing cases include at least one of the
mutant's own group. A mutant with group "hook" runs tests/test_gate_hook.py and is killed by any failure.
SURVIVED means a case is missing. CAPPED means the run hit the time or memory cap, which is reported
as its own outcome and never counted as a kill.

    python3 tests/tools/mutate_gate.py
"""
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "gate" / "patton_gate.py"

MUTANTS = [
    ("rm family", "rm", "        if name in RM_VERBS:\n            self._rm(name, words)", "        if False:\n            self._rm(name, words)"),
    ("rm recursion", "rm", 'recursive = name == "rm" and _cluster_has(flags, "rR", "--recursive")', "recursive = False"),
    ("mv/cp/ln family", "move", 'elif name in ("mv", "cp", "install", "ln"):\n            self._move(name, words)', 'elif False:\n            self._move(name, words)'),
    ("mv sources", "move", "                if name == \"mv\":\n                    for s in sources:", "                if False:\n                    for s in sources:"),
    ("write family", "write", 'elif name in ("truncate", "tee", "touch", "chmod", "chown", "chgrp", "dd", "mkdir"):', "elif False:"),
    ("dd of=", "write", 'if w.text.startswith("of="):', "if False:"),
    ("sed -i", "sed", "        if in_place:\n            self.mutates = True", "        if False:\n            self.mutates = True"),
    ("sed script that writes", "sed", "if len(script) > MAX_SCRIPT or self._SED_WRITE.search(script) or self._SED_CMD.search(script):", "if False:"),
    ("redirections", "redirect", "        self._redirects(redirs)\n        while words and ASSIGNMENT", "        while words and ASSIGNMENT"),
    ("redirect to /dev", "read", 'if target.text.startswith("/dev/"):\n                continue', "if False:\n                continue"),
    ("find -delete", "find", 'if "-delete" in expr:', "if False:"),
    ("find -ok and file outputs", "find", 'if any(t in ("-ok", "-okdir") or t.startswith(("-fprint", "-fls")) for t in expr):', "if False:"),
    ("find -exec command", "find", 'if k + 1 >= len(expr) or expr[k + 1] not in FIND_EXEC_READ_ONLY:', "if k + 1 >= len(expr):"),
    ("find -exec terminator", "find", 'if end is None:\n                    raise _Opaque("find -exec without its terminator")', 'if end is None:\n                    end = len(expr)'),
    ("git -C change", "git", 'if moved:  # -C moves', 'if False:  # -C moves'),
    ("xargs command", "find", 'if i >= len(words) or words[i].text not in FIND_EXEC_READ_ONLY:\n            raise _Opaque("xargs with a command', 'if i >= len(words):\n            raise _Opaque("xargs with a command'),
    ("xargs options", "find", '            else:\n                raise _Opaque("xargs with an option the gate does not read")', '            else:\n                i += 1'),
    ("xargs value flags", "find", '            elif text in XARGS_VALUE_FLAGS:\n                i += 2', '            elif text in XARGS_VALUE_FLAGS:\n                i += 1'),
    ("git rm", "git", 'if sub == "rm":', "if False:"),
    ("git checkout --", "git", 'elif sub == "checkout":', "elif False:"),
    ("git unknown subcommand", "git", 'raise _Opaque(f"git {sub}")', "return"),
    ("unknown command", "opaque", 'raise _Opaque(f"unknown command: {name}")', "return"),
    ("bare command substitution", "opaque", '            if nxt == "(":\n                raise _Opaque("command substitution")\n            if nxt in', '            if nxt in'),
    ("quoted command substitution", "opaque", '                    if nxt == "(":\n                        raise _Opaque("command substitution")\n                    if nxt and', '                    if nxt and'),
    ("backticks", "opaque", '        if c == "`":\n            raise _Opaque("command substitution")', "        if False:\n            pass"),
    ("path expansion", "opaque", "        if word.expands or word.brace:\n            raise _Opaque", "        if False:\n            raise _Opaque"),
    ("path-run programs", "opaque", "if os.path.dirname(text) not in SAFE_BIN_DIRS:", "if False:"),
    ("here-document substitution", "opaque", 'if not quoted and any("$(" in line or "`" in line for line in body):', "if False:"),
    ("compound for/case", "opaque", "            if kw in COMPOUND_OPAQUE:\n                raise", "            if False:\n                raise"),
    ("glob expansion", "rm", "        if word.glob:\n            base =", "        if False:\n            base ="),
    ("symlink target on write", "redirect", "self._add(self._real(p, cwd, True))", "self._add(self._real(p, cwd, False))"),
    ("cd tracking", "compound", "        self.cwds = fresh if certain else self.cwds | fresh", "        pass"),
    ("subshell cwd restore", "compound", "                walker.cwds, walker.cond_depth = stack.pop()", "                stack.pop()"),
    ("wrappers", "rm", 'if text in ("env", "sudo", "nohup", "command", "nice"):', "if False:"),
    ("assignment prefix", "rm", "        while words and ASSIGNMENT.match(words[0].text) and not words[0].quoted:\n            words = words[1:]", "        pass"),
    ("keyword skipping", "compound", "        while words and not words[0].quoted and words[0].text in KEYWORDS | COMPOUND_OPAQUE:", "        while False:"),
    ("oversize command", "test:test_a_command_too_long_to_read_is_refused", "        if len(command) > MAX_COMMAND:\n            raise", "        if False:\n            raise"),
]

HOOK_MUTANTS = [
    ("protected-path check on edits", 'rel in state.protected and rel not in state.granted else ()', 'False else ()'),
    ("manifest root check", 'if manifest.get("root") != root_real:', 'if False:'),
    ("approver failure is a refusal", 'except (OSError, ValueError, subprocess.SubprocessError, UnicodeDecodeError):\n        return refused',
     'except (OSError, ValueError, subprocess.SubprocessError, UnicodeDecodeError):\n        return {"approved": list(decision.paths), "denied": []}'),
    ("approver answer shape", "    if (not isinstance(answer, dict) or not isinstance(answer.get(\"approved\"), list)", "    if (False and not isinstance(answer.get(\"approved\"), list)"),
    ("grants persist", 'state.grant(answer["approved"])', 'pass'),
    ("extra approvals ignored", 'approved = [p for p in decision.paths if p in set(answer["approved"])]', 'approved = answer["approved"]'),
    ("unknown tool", 'if tool not in EDIT_TOOLS and tool != "Bash":', 'if False:'),
    ("hook event name", 'if name != "PreToolUse":', 'if False:'),
    ("safe tools", 'if tool in SAFE_TOOLS:\n        return Decision("allow")', 'if False:\n        return Decision("allow")'),
    ("event cwd", 'cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]) else state.root', 'cwd = state.root'),
    ("opaque is denied", 'if found.kind == OPAQUE:', 'if False:'),
    ("denial limit", 'if state.denials >= DENIAL_LIMIT:\n        return Decision("deny"', 'if False:\n        return Decision("deny"'),
    ("denial counting", 'if decision.note != "stalled":\n            state.count_denial()', 'if False:\n            state.count_denial()'),
    ("unconfigured gate", 'if not state_dir or not root:\n        return _emit(stdout, "deny"', 'if False:\n        return _emit(stdout, "deny"'),
    ("post-tool-use completes an ask", 'if tool_use_id in pending:', 'if False:'),
    ("ledger line", 'state.record({**answered, "verdict": "approved"})', 'pass'),
    ("partial approval message", 'if answer["approved"]:  # the whole call is blocked', 'if False:  # the whole call is blocked'),
]


def cap():
    resource.setrlimit(resource.RLIMIT_AS, (6 << 30, 6 << 30))


def run(gate_dir: str, module: str = "tests.test_gate_corpus") -> tuple[str, str]:
    env = dict(os.environ, PATTON_GATE_DIR=gate_dir)
    try:
        proc = subprocess.run([sys.executable, "-m", "unittest", module], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=180, preexec_fn=cap)
    except subprocess.TimeoutExpired:
        return "CAPPED", ""
    return ("PASS" if proc.returncode == 0 else "FAIL"), proc.stderr


def main() -> int:
    source = GATE.read_text()
    work = Path(tempfile.mkdtemp(prefix="mutate-gate-"))
    try:
        base = work / "base"
        base.mkdir()
        shutil.copy(GATE, base / "patton_gate.py")
        status, _ = run(str(base))
        hook_status, _ = run(str(base), "tests.test_gate_hook")
        print(f"baseline: corpus {status}, hook {hook_status}")
        if status != "PASS" or hook_status != "PASS":
            return 1
        bad = 0
        everything = [(n, g, o, w, "tests.test_gate_corpus") for n, g, o, w in MUTANTS] + \
                     [(n, "hook", o, w, "tests.test_gate_hook") for n, o, w in HOOK_MUTANTS]
        for i, (name, group, old, new, module) in enumerate(everything):
            if source.count(old) != 1:
                print(f"!! mutant {name!r}: pattern occurs {source.count(old)} times, not once")
                bad += 1
                continue
            mdir = work / f"m{i}"
            mdir.mkdir()
            (mdir / "patton_gate.py").write_text(source.replace(old, new))
            if (mdir / "patton_gate.py").read_text() == source:
                print(f"!! mutant {name!r}: the mutation did not change the file")
                bad += 1
                continue
            status, err = run(str(mdir), module)
            failing = re.findall(r"^\[(\w+)\] (.*?): want", err, re.M)
            groups = sorted({g for g, _ in failing} | {f"test:{t}" for t in re.findall(r"^FAIL: (\w+)", err, re.M)})
            if status == "CAPPED":
                outcome = "CAPPED"
            elif status == "FAIL" and (group == "hook" or group in groups):
                outcome = "KILLED"
            elif status == "FAIL":
                outcome = f"KILLED, but not in its own group {group!r} (failed groups: {groups or 'tests other than the corpus'})"
            else:
                outcome = "SURVIVED"
            if not outcome.startswith("KILLED") or "but not" in outcome or outcome == "CAPPED":
                bad += 1
            print(f"{outcome:8} {name:32} group={group:9} failing cases={len(failing)}")
        status, _ = run(str(base))
        hook_status, _ = run(str(base), "tests.test_gate_hook")
        status = "PASS" if status == hook_status == "PASS" else "FAIL"
        print(f"restored baseline: {status}")
        print(f"{len(everything)} mutants, {bad} not cleanly killed")
        return 0 if bad == 0 and status == "PASS" else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
