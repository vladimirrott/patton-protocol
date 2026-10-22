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
    (RU, 'label, None, kept["calls"])\n        problems += found', 'label, None, kept["calls"])\n        pass'),
    (M, 'return list(entry.get("paths") or []) if entry.get("verdict") == "approved" else []', 'return list(entry.get("paths") or [])'),
    (RU, 'first = max(kept) + 1 if kept else 0', 'first = 0'),
    (RU, 'expected[f"{out_dir.name}/{record[\'run_id\']}#a{record[\'attempt\']}"] = attempt_cost(taped)[0]', 'expected[f"{out_dir.name}/{record[\'run_id\']}#a{record[\'attempt\']}"] = attempt_cost(record["calls"])[0]'),
    (RU, "    elif [(c[\"input_tokens\"], c[\"output_tokens\"]) for c in taped] != [(c[\"input_tokens\"], c[\"output_tokens\"]) for c in calls]:", "    elif False:"),
    (R, 'message["usage"].update(event.get("usage") or {})\n        elif kind == "error"', 'pass\n        elif kind == "error"'),
    (R, 'headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}', 'headers = dict(self.headers.items())'),
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
