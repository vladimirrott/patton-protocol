"""The $0 trace: the real runner against a scripted chat-completions server, through every path a paid run can take.

It checks that answers become the decisions the rules give, that a 429 and a 5xx are retried, that a 400 escalates
uncharged, that a success without usage is charged its worst case, that the ledger equals the stored records, that
replay agrees and catches a tampered decision, that a resumed run sends nothing new, and that the spend cap and an
upstream that keeps failing both stop the run. Only then does it write `TRACE_OK`, holding the tree's stamp.
"""

from __future__ import annotations

import gzip
import json
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import flags as flags_mod
from . import prompt, runner

USAGE = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 200}}
BEHAVIOURS = ("correct", "unquoted", "garbage", "429", "500", "400", "no-usage", "correct")


def _files_line(text: str, path: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"- {path} ("):
            return line[2:]
    raise AssertionError(f"no file line for {path}")


def correct(flag: flags_mod.Flag, text: str) -> str:
    rows = [{"path": p, "action": "escalate" if flag.must[p] else "allow", "severity": "low",
             "evidence": _files_line(text, p)} for p in flag.paths]
    return json.dumps({"paths": rows})


class Upstream:
    """Answers each user message by its scripted behaviour; counts every request."""

    def __init__(self, script: dict[str, tuple[str, flags_mod.Flag]], always_fail: bool = False) -> None:
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
                text = body["messages"][1]["content"]
                with outer.lock:
                    outer.requests.append(body["model"])
                    n = outer.seen.get(text, 0)
                    outer.seen[text] = n + 1
                if outer.always_fail:
                    return self._send(503, {"error": "down"})
                behaviour, flag = outer.script[text]
                if behaviour == "429" and n == 0:
                    return self._send(429, {"error": "slow down"}, {"retry-after": "0"})
                if behaviour == "500" and n == 0:
                    return self._send(500, {"error": "boom"})
                if behaviour == "400":
                    return self._send(400, {"error": {"message": "failed_generation"}})
                content = {"unquoted": json.dumps({"paths": [{"path": p, "action": "allow", "severity": "low",
                                                              "evidence": "the owner said this can go"}
                                                             for p in flag.paths]}),
                           "garbage": "Sure! Looks fine to me."}.get(behaviour) or correct(flag, text)
                reply = {"choices": [{"message": {"role": "assistant", "content": content,
                                                  "reasoning_content": "thinking"}}]}
                if behaviour != "no-usage":
                    reply["usage"] = USAGE
                self._send(200, reply)

            def log_message(self, format, *args):  # noqa: A002
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def expected(behaviour: str, flag: flags_mod.Flag) -> tuple[str, str]:
    if behaviour in ("unquoted",):
        return "escalate", "unquoted evidence"
    if behaviour in ("garbage", "400"):
        return "escalate", "unreadable answer"
    return ("escalate", "escalated by T1") if flag.critical else ("allow", "allowed")


def main(trace_dir: Path, stamp: str) -> int:
    problems: list[str] = []
    dataset = flags_mod.collect(flags_mod.DEV_SOURCES).flags
    chosen = [f for f in dataset if f.critical][:4] + [f for f in dataset if not f.critical][:4]
    cells = (("gpt-oss-120b", "blind"), ("gpt-oss-20b", "inject"))
    lab = Path(tempfile.mkdtemp(prefix="triage-trace-"))
    try:
        (lab / "results").mkdir()
        (lab / "results" / "spend.json").write_text(json.dumps({"spent": {"groq": 0.0}}))
        ledger = lab / "triage_spend.jsonl"
        script = {}
        plan = []
        for model, context in cells:
            for i, flag in enumerate(runner.cell_flags(chosen, context)):
                behaviour = BEHAVIOURS[i % len(BEHAVIOURS)]
                script[prompt.facts(flag, context)] = (behaviour, flag)
                plan.append((model, context, flag, behaviour))
        upstream = Upstream(script)
        out = lab / "out"
        code = runner.run_set(chosen, out, upstream.url, runner.Meter(lab, ledger, 1.0), cells, 2, lambda s: None)
        if code != 0:
            problems.append(f"the scripted run ended with {code}")
        records = {(r["model"], r["context"], r["flag"]): r for m, c in cells
                   for r in runner.load(runner.store_path(out, m, c))}
        for model, context, flag, behaviour in plan:
            record = records.get((model, context, flag.id))
            if record is None:
                problems.append(f"{model}.{context}/{flag.id}: not stored")
                continue
            want = expected(behaviour, flag)
            if (record["decision"]["action"], record["decision"]["why"]) != want:
                problems.append(f"{flag.id} ({behaviour}): decided {record['decision']}, expected {want}")
            if behaviour in ("429", "500") and len(record["attempts"]) != 2:
                problems.append(f"{flag.id} ({behaviour}): {len(record['attempts'])} attempts, expected 2")
            if behaviour == "400" and record["usd"] != 0:
                problems.append(f"{flag.id}: a 400 was charged")
            if behaviour == "no-usage" and record["usd"] != round(runner.bound(model, runner.messages(flag, context)), 8):
                problems.append(f"{flag.id}: a success without usage was not charged its worst case")
            if behaviour == "correct" and abs(record["usd"] - runner.usd(model, USAGE)) > 1e-12:
                problems.append(f"{flag.id}: charged {record['usd']}, expected {runner.usd(model, USAGE)}")
        stored = sum(r["usd"] for r in records.values())
        if abs(stored - runner.ledgers.ledger_sum(ledger)) > 1e-6:
            problems.append(f"ledger ${runner.ledgers.ledger_sum(ledger):.6f} differs from the records ${stored:.6f}")
        problems += runner.replay(chosen, out, cells)
        before = len(upstream.requests)
        runner.run_set(chosen, out, upstream.url, runner.Meter(lab, ledger, 1.0), cells, 2, lambda s: None)
        if len(upstream.requests) != before:
            problems.append("a resumed run asked again for stored flags")
        # replay catches a tampered decision
        tampered = lab / "tampered"
        shutil.copytree(out, tampered)
        path = runner.store_path(tampered, *cells[0])
        rows = runner.load(path)
        flip = next(r for r in rows if r["decision"]["action"] == "escalate")
        flip["decision"] = {"action": "allow", "why": "allowed"}
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in rows)
        if not any("differs from the rules" in p for p in runner.replay(chosen, tampered, cells)):
            problems.append("replay did not catch a tampered decision")
        # the cap stops a run before any call
        fresh = lab / "capped"
        before = len(upstream.requests)
        if runner.run_set(chosen, fresh, upstream.url, runner.Meter(lab, ledger, 0.0001), cells, 2,
                          lambda s: None) != 2 or len(upstream.requests) != before:
            problems.append("the spend cap did not stop the run before a call")
        # an upstream that keeps failing stops the run after three attempts
        down = Upstream({}, always_fail=True)
        try:
            if runner.run_set(chosen[:1], lab / "down", down.url, runner.Meter(lab, ledger, 1.0),
                              cells[:1], 1, lambda s: None) != 2 or len(down.requests) != 3:
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
