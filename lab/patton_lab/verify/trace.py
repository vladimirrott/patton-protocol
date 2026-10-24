"""The $0 trace of the Verifier replay: the real runner against a scripted chat server, through every path.

The server answers the claim reader with a claim and the Verifier by script: a failure quoting the fixture's notes
for a harmful case, a pass for the rest, plus an unreadable answer, a 429, a 500 and a 400. The trace checks that
the stored decisions are the plugin's rules on those answers, that the ledger equals the records, that replay
agrees and catches a tampered decision, that a resumed run sends nothing new, and that the spend cap and a failing
upstream stop the run. Only then does it write `TRACE_OK` with the tree's stamp.
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
from . import runner

gate = cases_mod.gate
USAGE = {"prompt_tokens": 800, "completion_tokens": 60}


class Upstream:
    def __init__(self, script: dict[str, str], always_fail: bool = False) -> None:
        self.script, self.always_fail = script, always_fail
        self.requests: list[str] = []
        self.seen: dict[str, int] = {}
        self.lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: dict, headers: dict | None = None) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                system, user = body["messages"][0]["content"], body["messages"][1]["content"]
                with outer.lock:
                    outer.requests.append(system[:20])
                    n = outer.seen.get(user, 0)
                    outer.seen[user] = n + 1
                if outer.always_fail:
                    return self._send(503, {"error": "down"})
                if system == gate.CLAIMS_SYSTEM:
                    content = json.dumps({"claims_done": True, "quote": "Done."})
                else:
                    behaviour = outer.script[user]
                    if behaviour == "429" and n == 0:
                        return self._send(429, {"error": "slow"}, {"retry-after": "0"})
                    if behaviour == "500" and n == 0:
                        return self._send(500, {"error": "boom"})
                    if behaviour == "400":
                        return self._send(400, {"error": {"message": "bad"}})
                    if behaviour == "garbage":
                        content = "Looks fine to me."
                    elif behaviour == "fail":
                        quote = next(line for line in user.splitlines() if len(line.strip()) > 20 and "`" in line)
                        content = json.dumps({"verdict": "fail", "findings": [{"problem": "a needed file is gone",
                                                                                "evidence": quote}]})
                    else:
                        content = json.dumps({"verdict": "pass", "findings": []})
                self._send(200, {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": USAGE})

            def log_message(self, format, *args):  # noqa: A002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def expected(behaviour: str) -> str:
    return {"fail": "block", "garbage": "allow (not verified)", "400": "allow (not verified)"}.get(behaviour,
                                                                                                    "allow (passed)")


def main(trace_dir: Path, stamp: str) -> int:
    problems: list[str] = []
    dataset = cases_mod.collect(cases_mod.TEST_SOURCES)
    distinct: dict[str, cases_mod.Case] = {}
    for case in dataset:  # runs of one fixture often make the same change; the script is keyed by the input
        distinct.setdefault(cases_mod.verifier_text(case), case)
    pool = list(distinct.values())
    chosen = [c for c in pool if c.kind == "harmful"][:3] + [c for c in pool if c.kind == "clean"][:5]
    behaviours = ("fail", "fail", "fail", "pass", "garbage", "429", "500", "400")
    script = {cases_mod.verifier_text(c): b for c, b in zip(chosen, behaviours)}
    lab = Path(tempfile.mkdtemp(prefix="verify-trace-"))
    try:
        (lab / "results").mkdir()
        (lab / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": 0.0}}))
        ledger = lab / "verify_spend.jsonl"
        upstream = Upstream(script)
        models = ("gpt-oss-120b",)
        out = lab / "out"
        if runner.run_set(chosen, out, upstream.url, runner.Meter(lab, ledger, 1.0), models, 2, lambda s: None) != 0:
            problems.append("the scripted run did not complete")
        records = {r["case"]: r for r in runner.load(runner.store_path(out, models[0]))}
        for case, behaviour in zip(chosen, behaviours):
            record = records.get(case.id)
            if record is None:
                problems.append(f"{case.id}: not stored")
                continue
            if record["decision"] != expected(behaviour):
                problems.append(f"{case.id} ({behaviour}): decided {record['decision']!r}, expected {expected(behaviour)!r}")
            if behaviour in ("429", "500") and len(record["verifier"]["attempts"]) != 2:
                problems.append(f"{case.id} ({behaviour}): {len(record['verifier']['attempts'])} attempts, expected 2")
            if behaviour == "400" and record["verifier"]["usd"] != 0:
                problems.append(f"{case.id}: a 400 was charged")
            if behaviour == "fail" and not record["findings"]:
                problems.append(f"{case.id}: a quoted failure kept no finding")
        stored = sum(r["usd"] for r in records.values())
        if abs(stored - runner.spend.ledgers.ledger_sum(ledger)) > 1e-6:
            problems.append("the ledger differs from the records")
        problems += runner.replay(chosen, out, models)
        before = len(upstream.requests)
        runner.run_set(chosen, out, upstream.url, runner.Meter(lab, ledger, 1.0), models, 2, lambda s: None)
        if len(upstream.requests) != before:
            problems.append("a resumed run asked again")
        tampered = lab / "tampered"
        shutil.copytree(out, tampered)
        path = runner.store_path(tampered, models[0])
        rows = runner.load(path)
        rows[0]["decision"] = "something else"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in rows)
        if not any("differs from the rules" in p for p in runner.replay(chosen, tampered, models)):
            problems.append("replay did not catch a tampered decision")
        before = len(upstream.requests)
        if runner.run_set(chosen, lab / "capped", upstream.url, runner.Meter(lab, ledger, 0.0001), models, 2,
                          lambda s: None) != 2 or len(upstream.requests) != before:
            problems.append("the spend cap did not stop the run before a call")
        down = Upstream({}, always_fail=True)
        try:
            if runner.run_set(chosen[:1], lab / "down", down.url, runner.Meter(lab, ledger, 1.0), models, 1,
                              lambda s: None) != 2 or len(down.requests) != 3:
                problems.append(f"a failing upstream got {len(down.requests)} requests and did not stop the run")
        finally:
            down.close()
        upstream.close()
    finally:
        shutil.rmtree(lab, ignore_errors=True)
    if problems:
        print("\n".join(f"!! {p}" for p in problems))
        return 1
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "TRACE_OK").write_text(stamp + "\n", encoding="utf-8")
    print("trace OK: every path behaved; TRACE_OK written")
    return 0
