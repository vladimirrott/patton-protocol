"""The $0 trace of the P1M sweep: the real runner against a scripted chat server, through every path.

The server answers by script: a failure quoting the input, a pass, an unreadable answer, a truncated reply, a 429 and
a 5xx. The trace checks that each arm's request carries its model, reasoning setting and budget, that the stored
decisions are the plugin's rules on those answers, that the ledger equals the records, that replay agrees and names a
tampered decision, that a resumed run sends nothing new, and that the spend cap and a failing upstream stop the run.
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

from ..p1v import cases as cases_mod
from . import arms as arms_mod
from . import runner

gate = cases_mod.gate
EVIDENCE = "if month == 2 and (is_leap(year) or year == 2026):"
CASES = ("fail-quoted", "pass", "garbage", "truncated", "rate-limit-once")


class Upstream:
    def __init__(self, always_fail: bool = False) -> None:
        self.always_fail, self.requests, self.limited = always_fail, [], False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                user = body["messages"][1]["content"]
                if outer.always_fail:
                    return self.reply(500, {"error": {"message": "scripted"}})
                if "case:rate-limit-once" in user and not outer.limited:
                    outer.limited = True
                    return self.reply(429, {"error": {"message": "slow"}}, {"retry-after": "0"})
                finish, text = "stop", json.dumps({"verdict": "pass", "findings": []})
                if "case:fail-quoted" in user or "case:rate-limit-once" in user:
                    text = json.dumps({"verdict": "fail", "findings": [{"problem": "p", "evidence": EVIDENCE}]})
                elif "case:garbage" in user:
                    text = "looks fine"
                elif "case:truncated" in user:
                    finish, text = "length", ""
                self.reply(200, {"choices": [{"message": {"content": text}, "finish_reason": finish}],
                                 "usage": {"prompt_tokens": 900, "completion_tokens": 700}})

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
    return [cases_mod.Case(f"trace/{n}", "trace", "p01", 0, n == "fail-quoted", False, False, gate.VERIFIER_SYSTEM,
                           f"case:{n}\n## The change\n+    {EVIDENCE}\n", None, 1) for n in CASES]


def main(trace_dir: Path, stamp: str) -> int:
    problems: list[str] = []

    def check(label: str, ok: bool) -> None:
        if not ok:
            problems.append(label)

    dataset = trace_cases()
    names = runner.draw_names(arms_mod.ALL, 1)
    tmp = Path(tempfile.mkdtemp(prefix="p1m-trace-"))
    try:
        lab = tmp / "lab"
        (lab / "results").mkdir(parents=True)
        (lab / "results" / "spend.json").write_text(json.dumps({"spent": {"e1": 0.0}}), encoding="utf-8")
        ledger, out = lab / "p1m_spend.jsonl", tmp / "out"
        up = Upstream()
        try:
            code = runner.run_set(dataset, out, up.url, runner.Meter(lab, ledger, 5.0), names, per_arm=2,
                                  sleep=lambda s: None)
            check("the run did not complete", code == 0)
            seen = {(r["model"], r.get("reasoning_effort"), r.get("reasoning_format"), r["max_tokens"])
                    for r in up.requests}
            want = {(a.model, a.effort, "hidden" if a.hide else None, a.max_tokens) for a in arms_mod.ALL.values()}
            check(f"requests do not carry the arms' settings: {sorted(map(str, seen ^ want))}", seen == want)
            for name in names:
                records = {r["case"]: r for r in runner.load(runner.store_path(out, name))}
                check(f"{name}: not every case stored", set(records) == {c.id for c in dataset})
                flagged = {cid.split("/")[1] for cid, r in records.items() if r["flagged"]}
                check(f"{name}: wrong cases flagged {sorted(flagged)}", flagged == {"fail-quoted", "rate-limit-once"})
                trunc = records["trace/truncated"]
                check(f"{name}: a truncated reply was not recorded as one",
                      trunc["reply"]["finish_reason"] == "length" and not trunc["flagged"])
            lines = ledger.read_text(encoding="utf-8").splitlines()
            usd = sum(json.loads(line)["usd"] for line in lines)
            stored = sum(r["usd"] for n in names for r in runner.load(runner.store_path(out, n)))
            check("the ledger differs from the records", abs(usd - stored) < 1e-6 and bool(lines))
            before = len(up.requests)
            check("a resumed run did not complete", runner.run_set(dataset, out, up.url, runner.Meter(lab, ledger, 5.0),
                                                                    names, per_arm=2, sleep=lambda s: None) == 0)
            check("a resumed run sent calls", len(up.requests) == before)
            check("replay disagrees", runner.replay(dataset, out, names) == [])
            path = runner.store_path(out, "q38-high")
            records = runner.load(path)
            records[0]["flagged"] = not records[0]["flagged"]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(r) + "\n" for r in records))
            check("replay missed a tampered decision", bool(runner.replay(dataset, out, names)))
            capped = runner.run_set(dataset, tmp / "capped", up.url, runner.Meter(lab, tmp / "c_spend.jsonl", 0.001),
                                    names, per_arm=1, sleep=lambda s: None)
            check("the spend cap did not stop the run", capped == 2)
        finally:
            up.close()
        bad = Upstream(always_fail=True)
        try:
            failed = runner.run_set(dataset[:1], tmp / "failing", bad.url, runner.Meter(lab, tmp / "f_spend.jsonl", 5.0),
                                    runner.draw_names({"g": arms_mod.ALL["g120-high"]}, 1), per_arm=1,
                                    sleep=lambda s: None)
            check("a failing upstream did not stop the run", failed == 2)
        finally:
            bad.close()
        study, skipped = cases_mod.collect_stored(cases_mod.LAB / "fleet_p1r")
        check("the study set is not the 89 P1V used", len(study) == 89 and sum(c.gamed for c in study) == 41)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if problems:
        print("\n".join(f"!! {p}" for p in problems))
        return 1
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "TRACE_OK").write_text(stamp + "\n", encoding="utf-8")
    print(f"trace OK: stamp {stamp[:12]}")
    return 0
