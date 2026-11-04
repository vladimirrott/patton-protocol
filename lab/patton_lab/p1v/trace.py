"""The $0 trace of the P1V replay: the real runner against a scripted chat server, through every path.

The server answers by script: a failure that quotes the input, a pass, an unreadable answer, a failure whose
evidence is not in the input, a 429, and a 5xx. The trace checks that the stored decisions are the plugin's rules on
those answers, that the ledger equals the records, that replay agrees and names a tampered decision, that a resumed
run sends nothing new, and that the spend cap and a failing upstream stop the run. It also builds the real data sets.
Only then does it write `TRACE_OK` with the tree's stamp.
"""

from __future__ import annotations

import gzip
import json
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cases as cases_mod
from . import prompts as prompts_mod
from . import runner

gate = cases_mod.gate
USAGE = {"prompt_tokens": 800, "completion_tokens": 60}
EVIDENCE = "if month == 2 and (is_leap(year) or year == 2026):"
BEHAVIOUR = {"fail-quoted": "fail", "pass": "pass", "garbage": "garbage", "fail-invented": "invented"}


class Upstream:
    def __init__(self, always_fail: bool = False) -> None:
        self.always_fail, self.requests, self.rate_limited = always_fail, [], False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                user = body["messages"][1]["content"]
                outer.requests.append(user)
                if outer.always_fail:
                    return self.reply(500, {"error": {"message": "scripted"}})
                if "case:rate-limit-once" in user and not outer.rate_limited:
                    outer.rate_limited = True
                    return self.reply(429, {"error": {"message": "slow down"}}, {"retry-after": "0"})
                behaviour = next((b for key, b in BEHAVIOUR.items() if f"case:{key}" in user), "pass")
                text = {"fail": json.dumps({"verdict": "fail", "findings": [{"problem": "special-cases 2026",
                                                                              "evidence": EVIDENCE}]}),
                        "pass": json.dumps({"verdict": "pass", "findings": []}),
                        "garbage": "I think it looks fine.",
                        "invented": json.dumps({"verdict": "fail", "findings": [{"problem": "p", "evidence":
                                                                                "text that is nowhere in the input"}]})}[behaviour]
                self.reply(200, {"choices": [{"message": {"content": text}}], "usage": USAGE})

            def reply(self, status, data, headers=None):
                raw = json.dumps(data).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def trace_cases() -> list[cases_mod.Case]:
    names = list(BEHAVIOUR) + ["rate-limit-once"]
    return [cases_mod.Case(f"trace/{n}", "trace", "p01", 0, n == "fail-quoted", False, False, gate.VERIFIER_SYSTEM,
                           f"case:{n}\n## The change\n+    {EVIDENCE}\n", None, 1) for n in names]


def check(label: str, ok: bool, problems: list[str]) -> None:
    if not ok:
        problems.append(label)


def main(trace_dir: Path, stamp: str) -> int:
    problems: list[str] = []
    dataset = trace_cases()
    tmp = Path(tempfile.mkdtemp(prefix="p1v-trace-"))
    try:
        lab = tmp / "lab"
        (lab / "results").mkdir(parents=True)
        (lab / "results" / "spend.json").write_text(json.dumps({"spent": {"e1": 0.0}}), encoding="utf-8")
        ledger, out = lab / "p1v_spend.jsonl", tmp / "out"
        up = Upstream()
        try:
            code = runner.run_set(dataset, out, up.url, runner.Meter(lab, ledger, 1.0), per_model=2, sleep=lambda s: None)
            check("the run did not complete", code == 0, problems)
            for name in prompts_mod.ARMS:
                records = {r["case"]: r for r in runner.load(runner.store_path(out, name))}
                check(f"{name}: not every case stored", set(records) == {c.id for c in dataset}, problems)
                flagged = {cid.split("/")[1] for cid, r in records.items() if r["flagged"]}
                check(f"{name}: wrong cases flagged: {sorted(flagged)}", flagged == {"fail-quoted", "rate-limit-once"}
                      or flagged == {"fail-quoted"}, problems)
            lines = ledger.read_text(encoding="utf-8").splitlines()
            usd = sum(json.loads(line)["usd"] for line in lines)
            stored = sum(r["usd"] for n in prompts_mod.ARMS for r in runner.load(runner.store_path(out, n)))
            check("the ledger differs from the records", abs(usd - stored) < 1e-6 and lines, problems)
            before = len(up.requests)
            check("a resumed run did not complete", runner.run_set(dataset, out, up.url, runner.Meter(lab, ledger, 1.0),
                                                                    per_model=2, sleep=lambda s: None) == 0, problems)
            check("a resumed run sent calls", len(up.requests) == before, problems)
            check("replay disagrees", runner.replay(dataset, out) == [], problems)
            path = runner.store_path(out, "control")
            records = runner.load(path)
            records[0]["flagged"] = not records[0]["flagged"]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(r) + "\n" for r in records))
            check("replay missed a tampered decision", bool(runner.replay(dataset, out)), problems)
            capped = runner.run_set(dataset, tmp / "capped", up.url, runner.Meter(lab, tmp / "c_spend.jsonl", 0.0001),
                                    per_model=1, sleep=lambda s: None)
            check("the spend cap did not stop the run", capped == 2, problems)
        finally:
            up.close()
        bad = Upstream(always_fail=True)
        try:
            failed = runner.run_set(dataset[:1], tmp / "failing", bad.url, runner.Meter(lab, tmp / "f_spend.jsonl", 1.0),
                                    per_model=1, sleep=lambda s: None)
            check("a failing upstream did not stop the run", failed == 2, problems)
        finally:
            bad.close()
        study, skipped = cases_mod.collect_stored(cases_mod.LAB / "fleet_p1r")
        check(f"the study set is {len(study)} cases, not 89", len(study) == 89 and sum(c.gamed for c in study) == 41, problems)
        check("a stop run was dropped without being named", len(study) + len(skipped) == 112, problems)
        check("the shipped prompt is not the control", prompts_mod.ARMS["control"] == gate.VERIFIER_SYSTEM, problems)
        check("a candidate equals the control", len({v for v in prompts_mod.ARMS.values()}) == len(prompts_mod.ARMS), problems)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if problems:
        print("\n".join(f"!! {p}" for p in problems))
        return 1
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "TRACE_OK").write_text(stamp + "\n", encoding="utf-8")
    print(f"trace OK: stamp {stamp[:12]}")
    return 0
