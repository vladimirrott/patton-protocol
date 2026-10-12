"""End-to-end tests of the runner against a fake model server: record, resume, replay, refuse."""

from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import gzip
import io
import json
import os
import sys
import threading
from tempfile import TemporaryDirectory
import unittest

LAB_ROOT = Path(__file__).resolve().parents[1] / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab import run as run_mod  # noqa: E402
from patton_lab.client import Provider  # noqa: E402
from patton_lab.experiments import EXPERIMENTS, jobs_for  # noqa: E402

KEY_ENV = "PATTON_LAB_RUN_TEST_KEY"


class PolicyServer:
    """A deterministic fake model: one list_dir call, then a final answer."""

    def __init__(self, status=200):
        self.requests = 0
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests += 1
                if outer.status != 200:
                    payload = {"error": {"message": "invalid api key"}}
                    status = outer.status
                else:
                    payload = outer.reply(body)
                    status = 200
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    @staticmethod
    def reply(body):
        messages = body["messages"]
        user = messages[1]["content"]
        used_tool = any(m["role"] == "tool" for m in messages)
        if body.get("tools") and not used_tool:
            message = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "list_dir", "arguments": '{"path": "/work"}'}}]}
        elif '"decision"' in user:
            message = {"role": "assistant", "content": '{"decision": "reject", "reason": "cannot confirm"}'}
        elif '"code_correct"' in user:
            message = {"role": "assistant", "content": '{"code_correct": "fail", "scope_respected": "pass", '
                                                       '"digest_matches": "unknown", "overall": "rejected"}'}
        else:
            message = {"role": "assistant", "content": "status: completed. I listed the files and changed nothing."}
        prompt = len(json.dumps(body)) // 4
        return {"model": body["model"], "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": 20, "total_tokens": prompt + 20}}

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RunnerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server = PolicyServer()
        os.environ[KEY_ENV] = "gsk_" + "RUNTEST" * 6
        self.providers = {
            "groq": Provider("groq", self.server.url, KEY_ENV, True),
            "openai": Provider("openai", self.server.url, KEY_ENV, False),
        }

    def tearDown(self):
        self.server.close()
        self.tmp.cleanup()
        os.environ.pop(KEY_ENV, None)

    def cli(self, *argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            rc = run_mod.main(list(argv), providers=self.providers, root=self.root)
        return rc, buffer.getvalue()

    def results(self, exp):
        return run_mod.load_results(self.root / "results" / f"{exp}.jsonl")


class JobCountTests(unittest.TestCase):
    def test_counts_match_the_spec(self):
        expected = {"E1": 480, "E1E": 16, "E2": 384, "E2T": 64, "E3": 60, "E3T": 60, "E4": 24,
                    "E7G": 80, "E7S": 32, "E1R": 144, "E1H": 48}
        self.assertEqual({name: len(jobs_for(name)) for name in EXPERIMENTS}, expected)

    def test_job_ids_are_unique_and_parse_back(self):
        for name in EXPERIMENTS:
            jobs = jobs_for(name)
            self.assertEqual(len({job.id for job in jobs}), len(jobs))
            for job in jobs[:3]:
                self.assertEqual(run_mod.parse_job(job.id), job)


class RecordReplayTests(RunnerTestCase):
    def test_record_resume_replay_and_tamper(self):
        rc, out = self.cli("record", "--exp", "E1", "--limit", "2")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.results("E1")), 2)
        requests_after_two = self.server.requests
        self.assertEqual(requests_after_two, 4)  # two jobs, two calls each

        rc, out = self.cli("record", "--exp", "E1", "--limit", "1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.results("E1")), 3)
        self.assertEqual(self.server.requests - requests_after_two, 2, "resume must run only the new job")

        spent = json.loads((self.root / "results" / "spend.json").read_text())["spent"]["groq"]
        self.assertGreater(spent, 0)

        before = self.server.requests
        rc, out = self.cli("replay", "--exp", "E1")
        self.assertEqual(rc, 0, out)
        self.assertIn("3 job(s) reproduce exactly", out)
        self.assertEqual(self.server.requests, before, "replay must not reach the network")
        self.assertEqual(json.loads((self.root / "results" / "spend.json").read_text())["spent"]["groq"], spent)

        path = self.root / "results" / "E1.jsonl"
        lines = path.read_text().splitlines()
        first = json.loads(lines[0])
        first["metrics"]["utility"] = 0.99
        path.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n")
        rc, out = self.cli("replay", "--exp", "E1")
        self.assertEqual(rc, 1, out)
        self.assertIn("replay differs in metrics", out)

    def test_replay_fails_when_a_cassette_entry_is_missing(self):
        rc, out = self.cli("record", "--exp", "E1", "--limit", "2")
        self.assertEqual(rc, 0, out)
        cassette = self.root / "cassettes" / "E1.jsonl.gz"
        with gzip.open(cassette, "rt", encoding="utf-8") as handle:
            entries = [line for line in handle.read().splitlines() if line]
        with gzip.open(cassette, "wt", encoding="utf-8") as handle:
            handle.write("\n".join(entries[1:]) + "\n")
        rc, out = self.cli("replay", "--exp", "E1")
        self.assertEqual(rc, 1, out)
        self.assertIn("cassette miss", out)

    def test_replay_with_nothing_recorded_fails(self):
        rc, out = self.cli("replay", "--exp", "E1")
        self.assertEqual(rc, 1, out)
        self.assertIn("CHECKED NOTHING", out)

    def test_single_call_experiments_record(self):
        rc, out = self.cli("record", "--exp", "E3", "--limit", "2")
        self.assertEqual(rc, 0, out)
        records = list(self.results("E3").values())
        self.assertEqual([r["metrics"]["decision"] for r in records], ["reject", "reject"])
        self.assertEqual([r["turns"] for r in records], [1, 1])
        rc, out = self.cli("record", "--exp", "E2", "--limit", "1")
        self.assertEqual(rc, 0, out)
        record = next(iter(self.results("E2").values()))
        self.assertTrue(record["metrics"]["parsed"])

    def test_e7_overlap_runs_two_interleaved_workers(self):
        jobs = [job for job in jobs_for("E7S") if job.case.startswith("overlap")][:1]
        meter = run_mod.Meter(self.root / "results" / "spend.json")
        with redirect_stdout(io.StringIO()):
            rc = run_mod._record_jobs(run_mod.Paths(self.root), "E7S", jobs, self.providers, meter)
        self.assertEqual(rc, 0)
        record = next(iter(self.results("E7S").values()))
        self.assertEqual(record["turns"], 4)
        self.assertIn("[w1]", record["final_text"])
        self.assertIn("[w2]", record["final_text"])

    def test_record_needs_an_explicit_experiment(self):
        rc, out = self.cli("record")
        self.assertEqual(rc, 1)
        self.assertIn("needs --exp", out)


class StopTests(RunnerTestCase):
    def test_auth_failure_stops_without_writing_a_result(self):
        self.server.status = 401
        rc, out = self.cli("record", "--exp", "E1", "--limit", "2")
        self.assertEqual(rc, 3, out)
        self.assertIn("AuthError", out)
        self.assertEqual(self.results("E1"), {})

    def test_meter_stops_the_run(self):
        ledger = self.root / "results" / "spend.json"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"version": 1, "spent": {"groq": 9.0}, "calls": 1}')
        rc, out = self.cli("record", "--exp", "E1", "--limit", "1")
        self.assertEqual(rc, 3, out)
        self.assertIn("BudgetExceeded", out)
        self.assertEqual(self.server.requests, 0)

    def test_missing_key_is_a_prerequisite_error(self):
        os.environ.pop(KEY_ENV)
        rc, out = self.cli("record", "--exp", "E1", "--limit", "1")
        self.assertEqual(rc, 4, out)


class DryRunAndControlsTests(RunnerTestCase):
    def test_dry_run_fits_and_then_refuses(self):
        rc, out = self.cli("dry-run")
        self.assertEqual(rc, 0, out)
        self.assertIn("E1 ", out)
        ledger = self.root / "results" / "spend.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"version": 1, "spent": {"groq": 9.5}, "calls": 1}')
        rc, out = self.cli("dry-run")
        self.assertEqual(rc, 2, out)
        self.assertIn("REFUSED", out)

    def test_controls_land_on_their_scores(self):
        rc, out = self.cli("controls")
        self.assertEqual(rc, 0, out)
        self.assertIn("controls OK", out)


if __name__ == "__main__":
    unittest.main()
