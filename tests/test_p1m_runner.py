"""P1M: the frozen Verifier prompt asked of several models; payloads, arms, spend, resume and replay."""

import gzip
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lab"))

from patton_lab.p1m import arms as arms_mod  # noqa: E402
from patton_lab.p1m import runner  # noqa: E402
from patton_lab.p1v import cases as cases_mod  # noqa: E402
from patton_lab.p1v import prompts as p1v_prompts  # noqa: E402
from patton_lab.triage import runner as triage  # noqa: E402

EVIDENCE = "if month == 2 and (is_leap(year) or year == 2026):"
SOURCE = f"## The change\n+    {EVIDENCE}\n"


def make_case(cid="h/stop-p01-r0", gamed=True, source=SOURCE):
    return cases_mod.Case(cid, "h", "p01", 0, gamed, gamed, False, cases_mod.gate.VERIFIER_SYSTEM, source, None, 1)


class Upstream:
    def __init__(self, script):
        self.script, self.calls = script, []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(body)
                status, text, finish = outer.script(body)
                data = ({"choices": [{"message": {"content": text}, "finish_reason": finish}],
                         "usage": {"prompt_tokens": 1000, "completion_tokens": 500}} if status == 200
                        else {"error": {"message": "scripted"}})
                raw = json.dumps(data).encode()
                self.send_response(status)
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


def flag(body):
    return 200, json.dumps({"verdict": "fail", "findings": [{"problem": "p", "evidence": EVIDENCE}]}), "stop"


def lab_with(tmp: Path) -> Path:
    (tmp / "results").mkdir(parents=True)
    (tmp / "results" / "spend.json").write_text(json.dumps({"spent": {"e1": 0.0}}), encoding="utf-8")
    return tmp


TWO = {n: arms_mod.ALL[n] for n in ("q38-high", "g120-high")}


class ArmTests(unittest.TestCase):
    def test_every_arm_model_has_a_price_in_the_ledger_table(self):
        for arm in arms_mod.ALL.values():
            self.assertIn(arm.model, triage.MODELS, arm.name)

    def test_the_deprecated_model_is_not_an_arm_and_the_reference_is_not_asked_again(self):
        self.assertNotIn("qwen3.6-27b", {a.model for a in arms_mod.ALL.values()})
        self.assertNotIn(arms_mod.REFERENCE, arms_mod.ALL)

    def test_the_study_asks_the_two_qwen_arms_and_drops_the_rest_by_name(self):
        self.assertEqual(list(arms_mod.ARMS), ["q38-med", "q38-high"])
        self.assertEqual(set(arms_mod.ALL) - set(arms_mod.ARMS), set(arms_mod.DROPPED))
        self.assertTrue(all(a.model == "qwen3.8-27b" for a in arms_mod.ARMS.values()))

    def test_qwen_arms_hide_reasoning_and_gpt_oss_arms_never_send_a_format(self):
        for arm in arms_mod.ALL.values():
            extra = arm.extra()
            self.assertEqual("reasoning_format" in extra, arm.model.startswith("qwen"), arm.name)
            self.assertIn(extra["reasoning_effort"], ("low", "medium", "high"))

    def test_the_prompt_is_p1vs_frozen_candidate(self):
        self.assertEqual(arms_mod.SYSTEM, p1v_prompts.ARMS["fitted-v2"])

    def test_the_output_budget_leaves_room_for_reasoning_and_the_bound_uses_it(self):
        arm = arms_mod.ALL["q38-high"]
        self.assertEqual(arm.max_tokens, 16384)
        msgs = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
        small = arms_mod.Arm("s", "qwen3.8-27b", "high", max_tokens=4096)
        self.assertGreater(runner.bound(arm, msgs), runner.bound(small, msgs))

    def test_draw_names(self):
        got = runner.draw_names(TWO, 2)
        self.assertEqual(list(got), ["q38-high", "q38-high#2", "g120-high", "g120-high#2"])
        self.assertEqual(got["g120-high#2"][1], 2)


class RunTests(unittest.TestCase):
    def go(self, tmp, up, dataset, cap=5.0, names=None, lab=None):
        lab = lab or lab_with(Path(tmp) / "lab")
        meter = runner.Meter(lab, lab / "p1m_spend.jsonl", cap)
        out = Path(tmp) / "out"
        code = runner.run_set(dataset, out, up.url, meter, names or runner.draw_names(TWO, 1), per_arm=2,
                              sleep=lambda s: None)
        return code, out, lab

    def test_each_arm_gets_its_own_model_and_reasoning_setting(self):
        up = Upstream(flag)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.go(tmp, up, [make_case("a")])
            self.assertEqual(code, 0)
            by_model = {c["model"]: c for c in up.calls}
            self.assertEqual(by_model["qwen3.8-27b"]["reasoning_effort"], "high")
            self.assertEqual(by_model["qwen3.8-27b"]["reasoning_format"], "hidden")
            self.assertEqual(by_model["gpt-oss-120b"]["reasoning_effort"], "high")
            self.assertNotIn("reasoning_format", by_model["gpt-oss-120b"])
            self.assertTrue(all(c["max_tokens"] == 16384 for c in up.calls))
            self.assertTrue(all(c["messages"][0]["content"] == arms_mod.SYSTEM for c in up.calls))
            rec = runner.load(runner.store_path(out, "q38-high"))[0]
            self.assertTrue(rec["flagged"])
            self.assertEqual(rec["reply"]["finish_reason"], "stop")

    def test_a_truncated_reply_is_stored_with_its_finish_reason_and_allows(self):
        up = Upstream(lambda body: (200, "", "length"))
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            _, out, _ = self.go(tmp, up, [make_case("a")])
            rec = runner.load(runner.store_path(out, "q38-high"))[0]
        self.assertEqual((rec["reply"]["finish_reason"], rec["flagged"]), ("length", False))

    def test_resume_asks_nothing_new(self):
        up = Upstream(flag)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            _, _, lab = self.go(tmp, up, [make_case("a")])
            before = len(up.calls)
            code, _, _ = self.go(tmp, up, [make_case("a")], lab=lab)
        self.assertEqual((code, len(up.calls)), (0, before))

    def test_the_set_cap_and_the_lab_cap_stop_the_run(self):
        up = Upstream(flag)
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            code, _, _ = self.go(tmp, up, [make_case("a")], cap=0.001)
            self.assertEqual((code, up.calls), (2, []))
        with tempfile.TemporaryDirectory() as tmp:
            lab = lab_with(Path(tmp) / "lab")
            (lab / "other_spend.jsonl").write_text(json.dumps({"id": "x", "usd": 89.99}) + "\n", encoding="utf-8")
            code, _, _ = self.go(tmp, up, [make_case("a")], lab=lab)
            self.assertEqual((code, up.calls), (2, []))

    def test_repeated_server_errors_stop_and_store_nothing(self):
        up = Upstream(lambda body: (503, None, None))
        self.addCleanup(up.close)
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.go(tmp, up, [make_case("a")], names=runner.draw_names({"g": TWO["g120-high"]}, 1))
            self.assertEqual((code, runner.load(runner.store_path(out, "g120-high"))), (2, []))


class ReplayTests(unittest.TestCase):
    def stored(self, tmp):
        up = Upstream(flag)
        self.addCleanup(up.close)
        lab = lab_with(Path(tmp) / "lab")
        out = Path(tmp) / "out"
        names = runner.draw_names(TWO, 1)
        runner.run_set([make_case("a")], out, up.url, runner.Meter(lab, lab / "p1m_spend.jsonl", 5.0), names,
                       per_arm=1, sleep=lambda s: None)
        return names, out

    def test_stored_answers_redecide_the_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            names, out = self.stored(tmp)
            self.assertEqual(runner.replay([make_case("a")], out, names), [])

    def test_changed_input_setting_truth_decision_and_missing_case_are_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            names, out = self.stored(tmp)
            self.assertTrue(any("inputs changed" in p for p in runner.replay([make_case("a", source=SOURCE + "x")], out, names)))
            other = {**names, "q38-high": (arms_mod.Arm("q38-high", "qwen3.8-27b", "medium", hide=True), 1)}
            self.assertTrue(any("inputs changed" in p for p in runner.replay([make_case("a")], out, other)))
            self.assertTrue(any("truth changed" in p for p in runner.replay([make_case("a", gamed=False)], out, names)))
            self.assertTrue(any("missing" in p for p in runner.replay([make_case("a"), make_case("zz")], out, names)))
            path = runner.store_path(out, "g120-high")
            rec = runner.load(path)
            rec[0]["flagged"] = not rec[0]["flagged"]
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(r) + "\n" for r in rec))
            self.assertTrue(any("differs from the rules" in p for p in runner.replay([make_case("a")], out, names)))


if __name__ == "__main__":
    unittest.main()
