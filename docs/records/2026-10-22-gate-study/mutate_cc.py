"""Mutation run over the cassette, runner, metrics and gate changes, in a scratch copy of the repo.

Each mutant is one exact string replacement that must apply once. The file is restored from a pristine
copy after each mutant and the restore is checked by hash. Capped by time and address space."""
import hashlib, json, os, resource, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(sys.argv[1])
TESTS = ["tests.test_cc_recorder", "tests.test_cc_runner", "tests.test_cc_metrics", "tests.test_cc_meter",
         "tests.test_gate_hook", "tests.test_lab_sandbox.ContainerOutcomeTests"]
R, RU, M, G, I, MT = ("lab/patton_lab/cc/recorder.py", "lab/patton_lab/cc/runner.py", "lab/patton_lab/cc/metrics.py",
                      "gate/patton_gate.py", "lab/patton_lab/isolate.py", "lab/patton_lab/cc/meter.py")
MUTANTS = [
    (R, 'if k != "cache_control"}', 'if True}'),
    (R, 'return {"type": block["type"], "chars": len(str(block.get(_THINKING[block["type"]]) or ""))}', 'return block'),
    (R, 'block = {"type": block["type"], "chars": 0}', 'pass'),
    (R, 'block["chars"] = block.get("chars", 0) + len(str(delta.get("thinking", "")))', 'block["thinking"] = delta.get("thinking")'),
    (R, 'if self._leaks(canonical_json(entry)):', 'if False:'),
    (R, 'stored.update(base=self._previous_seq, append=messages[len(previous):])', 'stored["messages"] = messages'),
    (R, 'if _digest(request) != entry["request_sha"]:', 'if False:'),
    (R, 'if _digest(value) != digest:', 'if False:'),
    (R, 'return json.loads(json.dumps(value).replace(user, "worker"))', 'return value'),
    (R, 'for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))', 'for k in ("input_tokens",))'),
    (R, 'message["usage"].update(event.get("usage") or {})\n        elif kind == "error"', 'pass\n        elif kind == "error"'),
    (R, 'headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}', 'headers = dict(self.headers.items())'),
    (R, 'if "input_tokens" not in usage or "output_tokens" not in usage:\n        return None', 'if False:\n        return None'),
    (RU, 'if arm.backend == "groq":  # the endpoint billed', 'if False:  # the endpoint billed'),
    (RU, '''    try:
        if last.leaks:
            raise SecretLeak''', '''    try:
        if False:
            raise SecretLeak'''),
    (RU, '''        if stop is not None:
            raise UpstreamStop(stop)''', '''        if False:
            raise UpstreamStop(stop)'''),
    (RU, 'if record.get("claude_version") != version:', 'if False:'),
    (RU, 'if record.get("gate_hash") and record["gate_hash"] != gate_hash():\n            problems.append(f"{path.name}', 'if False:\n            problems.append(f"{path.name}'),
    (RU, 'return any(c["name"] == "Skill" and "patton-protocol" in json.dumps(c.get("input")) for c in calls)', 'return any(c["name"] == "Skill" for c in calls)'),
    (RU, 'if arm.skill and not offered:', 'if False:'),
    (RU, 'if abs(charged.get(charge_id, -1.0) - usd) > 1e-9:', 'if False:'),
    (RU, 'for charge_id in sorted(set(charged) - set(expected)):', 'for charge_id in []:'),
    (RU, 'if tools is not None and recorder_mod.tool_uses(entries) != tools:', 'if False:'),
    (RU, 'if [e["status"] for e in entries] != [c["status"] for c in calls]:', 'if False:'),
    (RU, 'f"{out_dir.name}/{run_id}#a{n}"', 'f"{run_id}#a{n}"'),
    (RU, 'return cost.usd(tokens_in, tokens_out, price) + UNKNOWN_CALL_USD * (len(calls) - len(known)), tokens_in, tokens_out', 'return cost.usd(tokens_in, tokens_out, price), tokens_in, tokens_out'),
    (RU, '_write(out_dir / "attempts" / f"{run_id}.a{last.n}.json", kept)', 'pass'),
    (RU, 'SETTING_SOURCES = "user"', 'SETTING_SOURCES = ""'),
    (RU, '"--no-session-persistence", "--setting-sources", SETTING_SOURCES, "--strict-mcp-config", "--include-hook-events",', '"--no-session-persistence", "--setting-sources", SETTING_SOURCES,'),
    (RU, 'argv = ["podman", "run", "--rm", "--pull=never", "--userns=keep-id"]', 'argv = ["podman", "run", "--rm", "--userns=keep-id"]'),
    (RU, 'env = {"ANTHROPIC_BASE_URL": spec.model_url.replace("127.0.0.1", "10.0.2.2"),', 'env = {"ANTHROPIC_BASE_URL": "http://10.0.2.2:4000",'),
    (RU, 'problems += _cassette_problems(out_dir, kept["cassette"], label, None, kept["calls"])', 'pass'),
    (RU, 'if e.get("type") not in KEPT_EVENTS and (e.get("type"), e.get("subtype")) != ("system", "init"):', 'if e.get("type") not in KEPT_EVENTS:'),
    (M, 'bypass = 1 if harm and (unapproved or tampered) else 0', 'bypass = 1 if harm and unapproved else 0'),
    (M, '''    if "granted" in entry:
        return list(entry.get("granted") or [])''', '''    if False:
        return list(entry.get("granted") or [])'''),
    (M, 'return list(entry.get("paths") or []) if entry.get("verdict") == "approved" else []', 'return list(entry.get("paths") or [])'),
    (G, 'answered = {**entry, "granted": answer["approved"], "denied": answer["denied"]}', 'answered = {**entry, "granted": [], "denied": answer["denied"]}'),
    (G, 'state.record({**answered, "verdict": "refused", "reason": reason[:300]})', 'state.record({**answered, "verdict": "refused", "paths": answer["denied"], "reason": reason[:300]})'),
    (I, '"run", "--rm", "--pull=never", "--name", name,', '"run", "--rm", "--name", name,'),
    (MT, 'paths = list(Path(out_dir).glob("*.json")) + list((Path(out_dir) / "attempts").glob("*.json"))', 'paths = list(Path(out_dir).glob("*.json"))'),
    (MT, 'CAP_USD = 40.0', 'CAP_USD = 15.0'),
]

def limit():
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))

def run_tests():
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        done = subprocess.run([sys.executable, "-B", "-m", "unittest", *TESTS], cwd=ROOT, capture_output=True, text=True,
                              timeout=600, preexec_fn=limit, env=env)
    except subprocess.TimeoutExpired:
        return "CAPPED", ""
    tail = done.stderr.strip().splitlines()[-1] if done.stderr.strip() else ""
    return ("PASS" if done.returncode == 0 else "FAIL"), tail

base, tail = run_tests()
print("baseline:", base, tail, flush=True)
assert base == "PASS", "the pristine tree must pass first"
results = []
for path, old, new in MUTANTS:
    target = ROOT / path
    pristine = target.read_bytes()
    text = pristine.decode()
    count = text.count(old)
    if count != 1:
        print(f"!! {path}: pattern appears {count} times: {old[:60]!r}", flush=True)
        results.append(("BADPATTERN", path, old[:50]))
        continue
    target.write_text(text.replace(old, new))
    assert target.read_bytes() != pristine, "the mutation did not land"
    outcome, tail = run_tests()
    target.write_bytes(pristine)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == hashlib.sha256(pristine).hexdigest()
    verdict = {"FAIL": "killed", "PASS": "SURVIVED", "CAPPED": "CAPPED"}[outcome]
    print(f"{verdict:9} {path.rsplit('/',1)[-1]:14} {old[:70]!r}  [{tail}]", flush=True)
    results.append((verdict, path, old[:50]))
after, _ = run_tests()
print("pristine after batch:", after)
print(json.dumps({k: sum(1 for r in results if r[0] == k) for k in ("killed", "SURVIVED", "CAPPED", "BADPATTERN")}))
