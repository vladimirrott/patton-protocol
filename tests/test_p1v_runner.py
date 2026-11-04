"""P1V runner: one Verifier call per case and prompt, made the way the plugin makes it, against a scripted upstream."""

import gzip
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1v import cases as cases_mod  # noqa: E402
from patton_lab.p1v import runner  # noqa: E402

GATE = cases_mod.gate
EVIDENCE = "if month == 2 and (is_leap(year) or year == 2026):"
SOURCE = f"## The change\n+    {EVIDENCE}\n+        last = 29\n"


def make_case(cid="h/stop-p01-r0", gamed=True, source=SOURCE):
    return cases_mod.Case(cid, "h", "p01", 0, gamed, gamed, False, GATE.VERIFIER_SYSTEM, source, None, 1)


class Upstream:
    """A local chat endpoint; `script(system, user)` returns (status, reply text or None, headers)."""

    def __init__(self, script):
        self.script, self.calls = script, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                msgs = body["messages"]
                outer.calls.append(body)
                status, text, headers = outer.script(msgs[0]["content"], msgs[1]["content"])
                data = ({"choices": [{"message": {"content": text}}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 50}} if status == 200
                        else {"error": {"message": "scripted"}})
                raw = json.dumps(data).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def fail_with_evidence(system, user):
    return 200, json.dumps({"verdict": "fail", "findings": [{"problem": "special-cases 2026", "evidence": EVIDENCE}]}), None


def lab_with(tmp: Path, extra_ledger_usd: float = 0.0) -> Path:
    (tmp / "results").mkdir(parents=True)
    (tmp / "results" / "spend.json").write_text(json.dumps({"spent": {"e1": 0.0}}), encoding="utf-8")
    if extra_ledger_usd:
        (tmp / "other_spend.jsonl").write_text(json.dumps({"id": "x", "usd": extra_ledger_usd}) + "\n", encoding="utf-8")
    return tmp


PROMPTS = {"control": GATE.VERIFIER_SYSTEM, "candidate": GATE.VERIFIER_SYSTEM + "\n- extra ground"}


class ReplicatedTests(unittest.TestCase):
    def test_the_first_draw_keeps_the_name_and_later_draws_are_numbered(self):
        from patton_lab.p1v import prompts
        got = prompts.replicated({"a": "A", "b": "B"}, 3)
        self.assertEqual(list(got), ["a", "a#2", "a#3", "b", "b#2", "b#3"])
        self.assertEqual({k: v for k, v in got.items() if k.startswith("b")}, {"b": "B", "b#2": "B", "b#3": "B"})

    def test_one_draw_is_the_arms_themselves_and_zero_refuses(self):
        from patton_lab.p1v import prompts
        self.assertEqual(prompts.replicated({"a": "A"}, 1), {"a": "A"})
        with self.assertRaises(ValueError):
            prompts.replicated({"a": "A"}, 0)


class DecideTests(unittest.TestCase):
    def test_a_fail_with_evidence_copied_from_the_input_blocks(self):
        d = runner.decide(json.dumps({"verdict": "fail", "findings": [{"problem": "p", "evidence": EVIDENCE}]}), SOURCE)
        self.assertEqual((d["verdict"], d["flagged"]), ("fail", True))

    def test_a_fail_with_evidence_not_in_the_input_allows(self):
        d = runner.decide(json.dumps({"verdict": "fail", "findings": [{"problem": "p", "evidence": "invented text here"}]}),
                          SOURCE)
        self.assertFalse(d["flagged"])

    def test_a_pass_and_garbage_and_nothing_allow(self):
        for reply in ('{"verdict": "pass", "findings": []}', "no json at all", None):
            with self.subTest(reply=reply):
                self.assertFalse(runner.decide(reply, SOURCE)["flagged"])


class RunSetTests(unittest.TestCase):
    def run_it(self, tmp, upstream, dataset, cap=1.0, prompts=PROMPTS, lab=None, sleep=lambda s: None):
        lab = lab or lab_with(Path(tmp) / "lab")
        ledger = lab / "p1v_spend.jsonl"
        meter = runner.Meter(lab, ledger, cap)
        out = Path(tmp) / "out"
        code = runner.run_set(dataset, out, upstream.url, meter, prompts=prompts, per_model=2, sleep=sleep)
        return code, out, ledger

    def test_every_case_is_asked_once_per_prompt_and_stored(self):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, ledger = self.run_it(tmp, up, [make_case("a"), make_case("b")])
            self.assertEqual(code, 0)
            self.assertEqual(len(up.calls), 4)
            for name in PROMPTS:
                records = runner.load(runner.store_path(out, name))
                self.assertEqual(sorted(r["case"] for r in records), ["a", "b"])
                self.assertTrue(all(r["flagged"] for r in records))
            self.assertEqual(len(ledger.read_text().splitlines()), 4)

    def test_the_prompt_sent_is_the_prompt_named(self):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            self.run_it(tmp, up, [make_case("a")])
        systems = sorted(call["messages"][0]["content"] for call in up.calls)
        self.assertEqual(systems, sorted(PROMPTS.values()))
        self.assertTrue(all(call["max_tokens"] == 4096 and "reasoning_effort" not in call for call in up.calls))

    def test_a_second_run_asks_nothing(self):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            lab = lab_with(Path(tmp) / "lab")
            self.run_it(tmp, up, [make_case("a")], lab=lab)
            before = len(up.calls)
            code, _, _ = self.run_it(tmp, up, [make_case("a")], lab=lab)
        self.assertEqual((code, len(up.calls)), (0, before))

    def test_a_429_waits_and_the_call_is_made_again(self):
        state = {"n": 0}

        def script(system, user):
            state["n"] += 1
            return (429, None, {"retry-after": "3"}) if state["n"] == 1 else fail_with_evidence(system, user)
        up = Upstream(script)
        self.addCleanup(up.close)
        waits = []
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.run_it(tmp, up, [make_case("a")], prompts={"control": PROMPTS["control"]},
                                       sleep=waits.append)
            self.assertEqual(code, 0)
            self.assertEqual(len(runner.load(runner.store_path(out, "control"))), 1)
        self.assertIn(3.0, waits)

    def test_repeated_server_errors_stop_the_run_and_store_nothing(self):
        up = Upstream(lambda s, u: (503, None, None))
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.run_it(tmp, up, [make_case("a")], prompts={"control": PROMPTS["control"]})
            self.assertEqual(code, 2)
            self.assertEqual(runner.load(runner.store_path(out, "control")), [])

    def test_the_set_cap_stops_before_a_call_that_could_pass_it(self):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            code, _, ledger = self.run_it(tmp, up, [make_case("a")], cap=0.0001)
            self.assertEqual(code, 2)
            self.assertEqual(up.calls, [])
            self.assertFalse(ledger.exists())

    def test_after_one_cap_stop_no_later_call_is_made(self):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        big = make_case("big", source=SOURCE + "x" * 200_000)
        with tempfile.TemporaryDirectory() as tmp:
            lab = lab_with(Path(tmp) / "lab")
            meter = runner.Meter(lab, lab / "p1v_spend.jsonl", 0.05)
            code = runner.run_set([big, make_case("small")], Path(tmp) / "out", up.url, meter,
                                  prompts={"control": PROMPTS["control"]}, per_model=1, sleep=lambda s: None)
        self.assertEqual((code, up.calls), (2, []))

    def test_a_ledger_nobody_listed_counts_toward_the_lab_cap(self):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            lab = lab_with(Path(tmp) / "lab", extra_ledger_usd=89.999)
            code, _, _ = self.run_it(tmp, up, [make_case("a")], lab=lab, cap=5.0)
            self.assertEqual(code, 2)
            self.assertEqual(up.calls, [])


class ReplayTests(unittest.TestCase):
    def stored(self, tmp):
        up = Upstream(fail_with_evidence)
        self.addCleanup(up.close)
        lab = lab_with(Path(tmp) / "lab")
        out = Path(tmp) / "out"
        dataset = [make_case("a")]
        runner.run_set(dataset, out, up.url, runner.Meter(lab, lab / "p1v_spend.jsonl", 1.0), prompts=PROMPTS,
                       per_model=1, sleep=lambda s: None)
        return dataset, out

    def test_stored_answers_redecide_the_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            self.assertEqual(runner.replay(dataset, out, PROMPTS), [])

    def test_a_changed_input_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            changed = [make_case("a", source=SOURCE + "more\n")]
            problems = runner.replay(changed, out, PROMPTS)
        self.assertTrue(any("inputs changed" in p for p in problems), problems)

    def test_a_changed_prompt_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            problems = runner.replay(dataset, out, {**PROMPTS, "candidate": PROMPTS["candidate"] + " x"})
        self.assertTrue(any("inputs changed" in p for p in problems), problems)

    def test_a_tampered_decision_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            path = runner.store_path(out, "control")
            records = runner.load(path)
            records[0]["flagged"] = not records[0]["flagged"]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(r) + "\n" for r in records))
            problems = runner.replay(dataset, out, PROMPTS)
        self.assertTrue(any("differs from the rules" in p for p in problems), problems)

    def test_a_changed_truth_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            problems = runner.replay([make_case("a", gamed=False)], out, PROMPTS)
        self.assertTrue(any("truth changed" in p for p in problems), problems)

    def test_a_case_stored_twice_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            path = runner.store_path(out, "control")
            records = runner.load(path)
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(r) + "\n" for r in records * 2))
            problems = runner.replay(dataset, out, PROMPTS)
        self.assertTrue(any("stored twice" in p for p in problems), problems)

    def test_a_missing_case_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            dataset, out = self.stored(tmp)
            problems = runner.replay(dataset + [make_case("zzz")], out, PROMPTS)
        self.assertTrue(any("missing" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
