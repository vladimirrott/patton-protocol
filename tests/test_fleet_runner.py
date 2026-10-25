"""The fleet runner end to end for $0, with an in-process stand-in for a harness port.

The stand-in follows the port contract (patton_lab/fleet/spec.py) without a container: it asks the recorder for the
next step, sends each shell call through the real bridge and gate when the arm has a gate, runs what is allowed in
the run's work directory, and feeds the result back. So the runner, the recorder's two routes, the gate's T1 call
through `{model_base}`, the scoring and the stamps are all checked here; the real ports' containers are checked by
`runner trace`.
"""

from pathlib import Path
from unittest import mock
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab import isolate  # noqa: E402
from patton_lab.fleet import runner, spec, suites  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402


def _post(port, body):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def fake_run(run_spec):
    messages = [{"role": "user", "content": run_spec.mission}]
    env = {"HOME": str(run_spec.home), "PATTON_GATE_ROOT": str(run_spec.work), "PATTON_GATE_STATE": str(run_spec.state),
           **run_spec.env}
    if run_spec.gate_config is not None:
        text = run_spec.config_file.read_text().replace("{model_base}", f"http://127.0.0.1:{run_spec.model_port}")
        run_spec.config_file.write_text(text)
        env["PATTON_GATE_CONFIG"] = str(run_spec.config_file)
    final = ""

    def hook(event):
        out = io.StringIO()
        bridge.main(["claude"], io.StringIO(json.dumps({"cwd": str(run_spec.work), "session_id": "s1", **event})),
                    out, env)
        return json.loads(out.getvalue()) if out.getvalue().strip() else {}

    for _ in range(8):
        message = _post(run_spec.model_port, {"model": run_spec.model, "messages": messages})["choices"][0]["message"]
        messages.append(message)
        if not message.get("tool_calls"):
            final = message.get("content") or ""
            if run_spec.gate_config is None:
                break
            answer = hook({"hook_event_name": "Stop", "last_assistant_message": final})
            if answer.get("decision") != "block":
                break
            messages.append({"role": "user", "content": answer["reason"]})
            continue
        for call in message["tool_calls"]:
            command = json.loads(call["function"]["arguments"])["command"]
            result, context = None, ""
            if run_spec.gate_config is not None:
                decision = hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": call["id"],
                                 "tool_input": {"command": command}}).get("hookSpecificOutput") or {}
                context = decision.get("additionalContext", "")
                if decision.get("permissionDecision") == "deny":
                    result = decision["permissionDecisionReason"]
            if result is None:
                done = subprocess.run(command, shell=True, cwd=run_spec.work, capture_output=True, text=True)
                result = done.stdout + done.stderr
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result + context})
    return spec.RunResult(0, False, final, 0.1)


FAKE_PORT = types.SimpleNamespace(VERSION="0-test", available=lambda: None, run=fake_run, __file__=__file__,
                                  STOP=True, CONTEXT=True,
                                  shell_step=lambda command: {"tool": "sh", "args": {"command": command}})


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        for patcher in (mock.patch.object(spec, "port", lambda name: FAKE_PORT),
                        mock.patch.object(runner, "TRACE_DIR", base / "trace"),
                        mock.patch.object(runner, "SCRATCH", base / "scratch")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_h1_trace_passes_every_arm_and_writes_the_stamp(self):
        lines = []
        self.assertEqual(runner.trace("h1", "pi", lines.append), [])
        self.assertEqual(len(lines), 3)
        self.assertEqual((runner.TRACE_DIR / "h1-pi.ok").read_text().strip(), runner.stamp("pi"))
        self.assertEqual(list((Path(self.tmp.name) / "scratch").iterdir()), [])  # each run's scratch is removed

    def test_the_trace_fails_when_the_gate_lets_the_audit_log_go(self):
        real = suites.E1.prepare

        def no_gate(kit, fixture_id, arm):
            prepared = real(kit, fixture_id, arm)
            return suites.Prepared(prepared.files, prepared.mission, None if arm == "rules" else prepared.config,
                                   prepared.git)
        with mock.patch.object(suites.E1, "prepare", no_gate):
            problems = runner.trace("h1", "pi", lambda s: None)
        self.assertTrue(any("rules: audit.log gone" in p for p in problems), problems)
        self.assertFalse((runner.TRACE_DIR / "h1-pi.ok").exists())

    @unittest.skipUnless(isolate.available(), "podman with the pinned image is not available")
    def test_the_p1_trace_reaches_the_reminder_the_refused_stop_and_the_verifier(self):
        lines = []
        self.assertEqual(runner.trace("p1", "pi", lines.append), [])
        self.assertEqual(len(lines), 4)
        self.assertTrue((runner.TRACE_DIR / "p1-pi.ok").exists())

    @unittest.skipUnless(isolate.available(), "podman with the pinned image is not available")
    def test_the_p1_trace_fails_when_the_stop_is_never_refused(self):
        real = suites.P1.prepare

        def no_stop_rule(kit, fixture_id, arm):
            prepared = real(kit, fixture_id, arm)
            if arm == "stop":
                prepared.config = {**prepared.config, "discipline": {"check_before_stop": False}}
            return prepared
        with mock.patch.object(suites.P1, "prepare", no_stop_rule):
            problems = runner.trace("p1", "pi", lambda s: None)
        self.assertTrue(any("stop: the refused stop never reached the model" in p for p in problems), problems)

    def test_the_pilot_refuses_without_a_matching_trace_and_never_reaches_the_proxy(self):
        def must_not(*a, **k):
            raise AssertionError("the pilot reached the network")
        with mock.patch.object(runner, "_proxy_up", must_not), mock.patch.object(runner, "run_one", must_not):
            self.assertEqual(runner.pilot("h1", ["pi"], 1, lambda s: None), 2)
            runner.TRACE_DIR.mkdir()
            (runner.TRACE_DIR / "h1-pi.ok").write_text("stale\n")
            self.assertEqual(runner.pilot("h1", ["pi"], 1, lambda s: None), 2)

    def test_a_stored_record_keeps_the_host_user_out(self):
        out = Path(self.tmp.name) / "out"
        record = {"harness": "pi", "arm": "rules", "fixture": "f04", "rep": 0, "final_text": str(Path.home())}
        runner._store(out, record, [{"route": "worker", "request": {"x": str(Path.home())}}])
        stored = runner.record_path(out, "pi", "rules", "f04", 0).read_text()
        self.assertNotIn(str(Path.home()), stored)
        self.assertEqual(json.loads(stored)["scrubbed"], 2)

    def test_the_cells_cover_the_set_once_in_a_fixed_order(self):
        cells = runner.cells("h1", ["pi", "omp"])
        self.assertEqual(len(cells), len(set(cells)))
        self.assertEqual(len(cells), 2 * 3 * 6)
        self.assertEqual(cells, runner.cells("h1", ["pi", "omp"]))


if __name__ == "__main__":
    unittest.main()
