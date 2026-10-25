#!/usr/bin/env python3
"""Mutation proof for the plugin's settings, subagent limits, kept copies and Codex mode.

Each mutant (name, old, new) replaces one exact snippet of plugins/patton-gate/scripts/patton_gate.py, runs the
plugin's tests, and puts the file back from a copy taken at the start, checking its hash, so uncommitted edits in the
file survive. KILLED means a test failed; SURVIVED means no test notices that guard; NOT APPLIED means the snippet
no longer occurs once (update the mutant); CAPPED means the run hit the time or memory cap, which never counts as a
kill. The tests these mutants run touch no network, no model and no container.

    python3 tests/tools/mutate_plugin.py
    python3 tests/tools/mutate_plugin.py --only verif --only outside   # mutants whose names hold any of these
"""

import hashlib
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "plugins" / "patton-gate" / "scripts" / "patton_gate.py"
TESTS = ["test_gate_config", "test_gate_missions", "test_gate_originals", "test_gate_codex", "test_gate_plugin",
         "test_gate_verify"]
MUTANTS = [
 ("dispatch total off by one", 'if missions["max_dispatches"] is not None and total >= missions["max_dispatches"]:', 'if missions["max_dispatches"] is not None and total > missions["max_dispatches"]:'),
 ("dispatch total ignored", 'if missions["max_dispatches"] is not None and total >= missions["max_dispatches"]:', 'if False:'),
 ("ledger drops changed paths", '    if decision.changed:\n        entry["changed"] = list(decision.changed)', '    pass'),
 ("choice tighten", "return CHOICES[key].index(new) >= CHOICES[key].index(current)", "return True"),
 ("bool tighten", "return bool(new) or not current", "return True"),
 ("list tighten", "    if key in LISTS:  # trusted_commands and ignore loosen whatever they add\n        return set(new) <= set(current)", "    if key in LISTS:\n        return True"),
 ("count tighten", "    if key in COUNTS:\n        return new <= current", "    if key in COUNTS:\n        return True"),
 ("limit tighten", "    return current is None or new <= current", "    return True"),
 ("null limit tighten", "    if new is None:  # a mission limit\n        return current is None", "    if new is None:\n        return True"),
 ("apply ignores tighten", "        if tighten_only and not _tighter(key, current, value):", "        if False:"),
 ("project not tighten-only", '"project file", True)', '"project file", False)'),
 ("trusted allows operators", 'any(item[0] != "word" or item[1].expands or item[1].brace for item in items)', 'any(item[0] == "word" and (item[1].expands or item[1].brace) for item in items)'),
 ("trusted allows expansion", 'item[0] != "word" or item[1].expands or item[1].brace', 'item[0] != "word"'),
 ("ignore not applied", 'return rel in self.protected and not _matches(files["ignore"], rel)', 'return rel in self.protected'),
 ("always_protect not applied", '        if _matches(files["always_protect"], rel):\n            return True', '        if False:\n            return True'),
 ("own files never hit", "                    hits.append(real if real in outside else os.path.relpath(real, self.root).replace(os.sep, \"/\"))", "                    pass"),
 ("retries off by one", "if retries is not None and tries > retries:", "if retries is not None and tries > retries + 1:"),
 ("workers off by one", "if workers is not None and busy >= workers:", "if workers is not None and busy > workers:"),
 ("reservations never expire", 'data["reservations"] = {k: t for k, t in data["reservations"].items() if now - t < RESERVATION_SECONDS}', 'pass'),
 ("start keeps reservation", '                data["reservations"].pop(oldest)', '                pass'),
 ("stop keeps owners", '            data["owners"] = {p: a for p, a in data["owners"].items() if a != agent}', '            pass'),
 ("failure keeps reservation", '            data["reservations"].pop(str(event.get("tool_use_id", "")), None)', '            pass'),
 ("refused dispatch counts", '        data["dispatches"][key] = tries + 1\n        _save_missions', '        _save_missions'),
 ("commands off by one", 'used > missions["max_commands"]', 'used > missions["max_commands"] + 1'),
 ("timeout never", 'now - started > missions["timeout_minutes"] * 60', 'now - started > missions["timeout_minutes"] * 6000'),
 ("tokens double count", 'seen[str(message.get("id"))] = int(message["usage"].get("output_tokens") or 0)', 'seen[str(message.get("id"))] = seen.get(str(message.get("id")), 0) + int(message["usage"].get("output_tokens") or 0)'),
 ("tokens strict", 'if tokens >= missions["max_output_tokens"]:', 'if tokens > missions["max_output_tokens"]:'),
 ("refused call claims", '        conflict = exclusive_conflict(event, state_dir, decision.changed, claim=False)', '        conflict = exclusive_conflict(event, state_dir, decision.changed, claim=True)'),
 ("claim not atomic", '        lost = exclusive_conflict(event, state_dir, decision.changed, claim=True) if exclusive else None', '        lost = None'),
 ("main claims paths", "        if claim and me and changed:", "        if claim and changed:"),
 ("snapshot every start", '(study or not os.path.exists(os.path.join(state_dir, "manifest.json")))', '(True)'),
 ("config error ignored", '    if snap.get("error"):\n        return _emit(stdout, "deny", f"Patton gate: the gate\'s configuration', '    if False:\n        return _emit(stdout, "deny", f"Patton gate: the gate\'s configuration'),
 ("on_change deny ignored", 'if spec is None and config["files"]["on_change"] == "deny" and decision.note != "own":', 'if False:'),
 ("unreadable allow ignored", 'if opaque and not study and (unreadable == "allow" or not config["files"]["protect"]):', 'if False:'),
 ("max_refusals fixed", 'if state.denials >= state.config["files"]["max_refusals"]:', 'if state.denials >= DENIAL_LIMIT:'),
 ("ancestor names nothing", "                            if self.root.startswith(real.rstrip(os.sep) + os.sep):", "                            if False:"),
 ("verifier sees the report", "        source = verifier_input(mission, changes, notes, checks, digest,", "        source = verifier_input(mission + final, changes, notes, checks, digest,"),
 ("verifier sees no file list", "digest, git_files(root, state_dir, digest))", "digest)"),
 ("file list from the start", "digest, git_files(root, state_dir, digest))", "digest, git_files(root, state_dir, turn['base']))"),
 ("surviving copy not named", '        change["before_id"] = ids.get(change["path"])', '        change["before_id"] = None'),
 ("twins not named", "            twins = [p for p in where[ident] if p != path]", "            twins = []"),
 ("listing cap off by one", "        for path, size, ident in files[:VERIFY_LIST_CAP]:", "        for path, size, ident in files[:VERIFY_LIST_CAP + 1]:"),
 ("listing cut silently", "            lines.append(f\"- and {len(files) - VERIFY_LIST_CAP} more files\")", "            pass"),
 ("claim reader sees the change", "claims_input(mission, final)))", "claims_input(mission + ''.join(c['diff'] for c in git_changes(root, state_dir, turn['base'], git_snapshot(root, state_dir))), final)))"),
 ("evidence not checked", "if parts and all(part in text for part in parts)", "if parts and True"),
 ("one quoted line is enough", "if parts and all(part in text for part in parts)", "if parts and any(part in text for part in parts)"),
 ("short evidence accepted", "max(map(len, parts)) >= VERIFY_MIN_EVIDENCE", "max(map(len, parts)) >= 0"),
 ("no round cap", '        if rounds >= cfg["max_rounds"]:', '        if False:'),
 ("rounds never counted", '                turns[scope]["rounds"] = rounds + 1', '                pass'),
 ("new prompt keeps rounds", 'turns[scope] = {"base": base, "rounds": 0,', 'turns[scope] = {"base": base, "rounds": old.get("rounds", 0),'),
 ("no claim still verifies", '        if not claims:\n            entry["decision"] = "allow (no completion claimed)"', '        if False:\n            entry["decision"] = "allow (no completion claimed)"'),
 ("unreadable claim skips", "    return True if claim is None or not isinstance", "    return False if claim is None or not isinstance"),
 ("error refuses the stop", '        return {"systemMessage": f"Patton verify: this change was not verified ({str(err)[:200]})."}', '        return {"decision": "block", "reason": "x"}'),
 ("notes from the candidate", '        notes = verify_notes(root, state_dir, turn["base"], cfg["notes"])', '        notes = verify_notes(root, state_dir, digest, cfg["notes"])'),
 ("snapshot into the repo", '    return {"GIT_OBJECT_DIRECTORY": objects, "GIT_ALTERNATE_OBJECT_DIRECTORIES": os.path.join(common, "objects")}', '    return {}'),
 ("snapshot uses the user index", '        env = {**env, "GIT_INDEX_FILE": index}', '        env = {**env}'),
 ("project sets the endpoint", "        if tighten_only and key in USER_ONLY:", "        if False:"),
 ("on without endpoint", '    if (verify["main"] or verify["subagents"]) and not (verify["endpoint"] and verify["model"]):', '    if False:'),
 ("refused subagent released", '        refused = name == "SubagentStop" and (verdict or {}).get("decision") == "block"  # it keeps running', '        refused = False'),
 ("stop falls through", '        if name in ("UserPromptSubmit", "Stop"):\n            return 0', '        if False:\n            return 0'),
 ("checks not run", '        checks = run_checks(root, cfg["checks"], cfg["check_timeout_s"]) if cfg["checks"] else []', '        checks = []'),
 ("subagent mission lost", '            mission = _mission_from_transcript(event.get("agent_transcript_path"))', '            mission = ""'),
 ("outside walked", "                        else:\n                            self.outside.add(real)\n", "                        else:\n                            self._files_under(real)\n                            self.outside.add(real)\n",
  # the whole suite would walk / under `rm -rf /` and hit the cap first; this test is the one aimed at the guard
  ["test_gate_config.AncestorRemovalTests.test_removing_a_directory_outside_the_project_names_nothing_and_walks_nothing"]),
 ("unknown field accepted", '            if name not in DEFAULTS[section]:\n                raise ConfigError(path, f"unknown field {key}")', '            if name not in DEFAULTS[section]:\n                continue'),
 ("limit type unchecked", "                if not ok or (name == \"timeout_minutes\"", "                if False and (name == \"timeout_minutes\""),
 ("preset not expanded", '    from_preset = {f"missions.{k}": v for k, v in PRESETS[str(preset)].items()} if preset is not None else {}', '    from_preset = {}'),
 ("glob any depth lost", '    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out))', '    return re.compile("".join(out))'),
 ("glob anchoring lost", '    return re.compile(("" if anchored else "(?:.*/)?") + "".join(out))', '    return re.compile("(?:.*/)?" + "".join(out))'),
 ("hold becomes pass", "        return _codex_hold(gate_event, environ, reason)", "        return None"),
 ("deny becomes pass", '    if verdict == "deny":\n        return _codex_deny(reason or "Patton gate: refused.")', '    if verdict == "deny":\n        return None'),
 ("no cd rewrite", '"updatedInput": {"command": f"cd {shlex.quote(cwd)} && {command}"}', '"updatedInput": {"command": command}'),
 ("patch not translated", '        gate_event = {**gate_event, "tool_name": "Bash", "tool_input": {"command": _patch_command(paths, found[1])}}', '        pass'),
 ("unreadable patch allowed", "        if paths is None:\n            return _codex_deny(", "        if False:\n            return _codex_deny("),
 ("heredoc ignored", '    found = (command, "") if tool == "apply_patch" else heredoc_patch(command)', '    found = (command, "") if tool == "apply_patch" else None'),
 ("unknown tool allowed", '    if tool not in CODEX_TOOLS or not isinstance(command, str):\n        return _codex_deny(', '    if not isinstance(command, str):\n        return _codex_deny('),
 ("relative cwd allowed", '    if not (isinstance(cwd, str) and os.path.isabs(cwd)):\n        return _codex_deny(', '    if False:\n        return _codex_deny('),
 ("bypass refuses instead of holding", '    gate_event = {**event, "permission_mode": "default"}', '    gate_event = dict(event)'),
 ("approval reusable", '            entry = holds.pop(held, None)', '            entry = holds.get(held)'),
 ("approval grants nothing", '            state.grant(need or [])', '            pass'),
 ("command approval lost", '            _approve_command(state_dir, str(need.get("command", "")))', '            pass'),
 ("crash fails open", '        answer = _codex_deny(f"Patton gate: the gate failed', '        answer = None and _codex_deny(f"Patton gate: the gate failed'),
 ("originals off", '    if not limit or not wanted:\n        return []', '    return []'),
 ("originals overwrite", '            if rel in index["kept"]:\n                kept.append(rel)\n                continue', '            if False:\n                continue'),
 ("originals ignore cap", '                if index["bytes"] + size > limit:', '                if False:'),
 ("originals keep new files", '    wanted = [rel for rel in changed if rel in state.protected and state.is_protected(rel)]', '    wanted = list(changed)'),
 ("promise without copy", '        if named and set(named) <= set(kept):', '        if named:'),
 ("restore clobbers", '                os.replace(target, target + ".before-restore")', '                os.remove(target)'),
 ("restore escapes", "        if not target.startswith(root_real + os.sep):", "        if False:"),
 ("restore unknown ok", "        if rel not in kept or not os.path.lexists(source):\n            stdout.write(f\"  not kept: {rel}\\n\")\n            failed += 1", "        if rel not in kept or not os.path.lexists(source):\n            stdout.write(f\"  not kept: {rel}\\n\")"),
 ("size tighten wrong", "    if key in SIZES:\n        return new >= current", "    if key in SIZES:\n        return True"),
 ("own files skip outside", '        for real in [os.path.join(self.root, rel) for rel in inside] + list(outside):', '        for real in [os.path.join(self.root, rel) for rel in inside]:'),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cap() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        pristine = Path(tmp) / "patton_gate.py"
        shutil.copy(TARGET, pristine)
        digest = sha(pristine)
        outcomes = []
        try:
            only = [a for flag, a in zip(sys.argv[1:], sys.argv[2:]) if flag == "--only"]
            for name, old, new, *chosen in MUTANTS:
                if only and not any(o in name for o in only):
                    continue
                text = pristine.read_text()
                if text.count(old) != 1:
                    outcomes.append((name, f"NOT APPLIED ({text.count(old)} matches)"))
                    continue
                TARGET.write_text(text.replace(old, new))
                if TARGET.read_text() == text:
                    outcomes.append((name, "NOT APPLIED (no change)"))
                    continue
                try:
                    done = subprocess.run([sys.executable, "-B", "-m", "unittest", *(chosen[0] if chosen else TESTS)], cwd=REPO / "tests",
                                          capture_output=True, text=True, timeout=300, preexec_fn=cap,
                                          env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin",
                                               "HOME": str(Path.home())})
                    outcomes.append((name, "KILLED" if done.returncode else "SURVIVED"))
                except subprocess.TimeoutExpired:
                    outcomes.append((name, "CAPPED"))
                finally:
                    shutil.copy(pristine, TARGET)
                    if sha(TARGET) != digest:
                        raise SystemExit(f"could not restore {TARGET}; the copy is at {pristine}")
        finally:
            shutil.copy(pristine, TARGET)
    for name, outcome in outcomes:
        print(f"{outcome:12} {name}")
    bad = [n for n, o in outcomes if o != "KILLED"]
    print(f"{len(outcomes) - len(bad)} of {len(outcomes)} killed; restored: {sha(TARGET) == digest}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
