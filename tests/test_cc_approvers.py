"""The five approvers and the loopback server that serves one run's approver to the gate."""

from pathlib import Path
import json
import sys
import unittest
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab.cc import approvers  # noqa: E402
from patton_lab.fixtures import e1  # noqa: E402

ALL_FIXTURES = e1.E1_FIXTURES + e1.E1_HELDOUT


def all_paths(fixture):
    return sorted(fixture.files)


class ApproverTests(unittest.TestCase):
    def test_oracle_matches_the_lab_rule_on_every_fixture(self):
        for fixture in ALL_FIXTURES:
            lab_rule = e1.oracle_approver(fixture)
            ours = approvers.make_approver("oracle", fixture, "run")
            for path in all_paths(fixture):
                self.assertEqual(ours([path]), lab_rule([path]), f"{fixture.id} {path}")

    def test_oracle_approves_something_and_denies_something(self):
        approved = denied = 0
        for fixture in ALL_FIXTURES:
            a, d = approvers.make_approver("oracle", fixture, "run")(all_paths(fixture))
            approved += len(a)
            denied += len(d)
        self.assertGreater(approved, 20)
        self.assertGreater(denied, 20)

    def test_deny_all_and_approve_all(self):
        fixture = e1.fixture_by_id("f01")
        paths = all_paths(fixture)
        self.assertEqual(approvers.make_approver("deny-all", fixture, "r")(paths), ([], paths))
        self.assertEqual(approvers.make_approver("approve-all", fixture, "r")(paths), (paths, []))

    def test_fatigue_3_follows_the_oracle_for_three_requests_then_approves_everything(self):
        fixture = e1.fixture_by_id("f01")
        only_copy = [g.paths[0] for g in fixture.groups if g.category == "IRR"][0]
        tired = approvers.make_approver("fatigue-3", fixture, "r")
        answers = [tired([only_copy]) for _ in range(5)]
        self.assertEqual(answers, [([], [only_copy])] * 3 + [([only_copy], [])] * 2)

    def test_fatigue_counts_requests_and_not_paths(self):
        fixture = e1.fixture_by_id("f01")
        paths = all_paths(fixture)
        tired = approvers.make_approver("fatigue-3", fixture, "r")
        first = tired(paths)
        self.assertEqual(first, e1.oracle_approver(fixture)(paths))

    def test_noisy_10_is_seeded_by_run_and_path(self):
        fixture = e1.fixture_by_id("f01")
        paths = all_paths(fixture)
        a = approvers.make_approver("noisy-10", fixture, "run-a")
        again = approvers.make_approver("noisy-10", fixture, "run-a")
        self.assertEqual([a([p]) for p in paths], [again([p]) for p in paths])

    def test_noisy_10_draws_different_noise_for_different_runs(self):
        fixture = e1.fixture_by_id("f01")
        oracle = e1.oracle_approver(fixture)
        paths = [f"p{i}.txt" for i in range(400)]

        def flips(run_id):
            noisy = approvers.make_approver("noisy-10", fixture, run_id)
            return {p for p in paths if noisy([p]) != oracle([p])}

        self.assertNotEqual(flips("run-a"), flips("run-b"))

    def test_noisy_10_flips_about_one_answer_in_ten(self):
        fixture = e1.fixture_by_id("f01")
        oracle = e1.oracle_approver(fixture)
        flipped = total = 0
        noisy = approvers.make_approver("noisy-10", fixture, "flip-rate")
        for i in range(4000):
            path = f"p{i}.txt"
            flipped += noisy([path]) != oracle([path])
            total += 1
        self.assertTrue(0.08 <= flipped / total <= 0.12, flipped / total)

    def test_unknown_approver_name_is_refused(self):
        with self.assertRaises(KeyError):
            approvers.make_approver("kindly", e1.fixture_by_id("f01"), "r")


def post(port, body, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{port}/approve", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = approvers.ApproverServer()
        self.fixture = e1.fixture_by_id("f01")
        self.port = self.server.start(approvers.make_approver("oracle", self.fixture, "run-1"))
        self.addCleanup(self.server.stop)

    def test_server_answers_and_records_requests(self):
        disposable = [g.paths[0] for g in self.fixture.groups if g.category == "DISP"][0]
        answer = post(self.port, {"run": "run-1", "paths": [disposable], "tool": "Bash", "command": "rm x"})
        self.assertEqual(answer, {"approved": [disposable], "denied": []})
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.requests[0]["paths"], [disposable])
        self.assertEqual(self.server.requests[0]["answer"], answer)

    def test_server_binds_loopback_only(self):
        self.assertEqual(self.server.address[0], "127.0.0.1")

    def test_server_survives_a_malformed_request(self):
        for raw in (b"not json", b"[]", json.dumps({"paths": "a.csv"}).encode(), json.dumps({}).encode()):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                post(self.port, None, raw=raw)
            self.assertEqual(caught.exception.code, 400)
        answer = post(self.port, {"run": "run-1", "paths": ["a.csv"], "tool": "Bash", "command": ""})
        self.assertEqual(answer["denied"], ["a.csv"])

    def test_a_stopped_server_refuses_connections(self):
        self.server.stop()
        with self.assertRaises(urllib.error.URLError):
            post(self.port, {"paths": []})


if __name__ == "__main__":
    unittest.main()
