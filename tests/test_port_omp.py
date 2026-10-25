"""oh-my-pi's port: the dialect's stop and reminder answers through the real bridge and gate, and the fleet port's
container command and run, without a container. The shared fleet kit is tested in test_port_pi.py."""

from pathlib import Path
from unittest import mock
import io
import json
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import omp as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.dialects import omp as dialect  # noqa: E402
from patton_lab.harness.pi import fleetkit  # noqa: E402


class OmpDialectStopTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("only copy\n")
        self.config = self.tmp / "config.json"
        self.env = {"HOME": str(self.tmp), "PATTON_GATE_ROOT": str(self.root), "PATTON_GATE_STATE": str(self.tmp / "state"),
                    "XDG_CONFIG_HOME": str(self.tmp / "xdg"), "PATTON_GATE_CONFIG": str(self.config)}

    def call(self, payload):
        out = io.StringIO()
        code = bridge.main(["omp"], io.StringIO(json.dumps(payload)), out, self.env)
        return code, json.loads(out.getvalue())

    def bash(self, command):
        return self.call({"tool": "bash", "input": {"command": command}, "cwd": str(self.root), "session": "s1"})

    def stop(self, active=False):
        return self.call({"event": "Stop", "final": "done", "active": active, "cwd": str(self.root), "session": "s1"})

    def test_a_stop_payload_parses_as_a_stop_with_its_active_flag(self):
        self.assertEqual(dialect.parse({"event": "Stop", "final": "done", "active": True, "cwd": "/work", "session": "s"}),
                         {"event": "Stop", "final": "done", "cwd": "/work", "session": "s", "active": True})
        with self.assertRaises(ValueError):
            dialect.parse({"event": "Stop"})

    def test_stop_and_context_answers(self):
        self.assertEqual(json.loads(dialect.emit_stop("block", "run the tests")[0]), {"block": True, "reason": "run the tests"})
        self.assertEqual(json.loads(dialect.emit_stop("allow", "")[0]), {"block": False})
        self.assertEqual(json.loads(dialect.emit_context("allow", "", "a test")[0]), {"block": False, "context": "a test"})

    def test_check_before_stop_refuses_once_then_allows(self):
        self.config.write_text(json.dumps({"discipline": {"check_before_stop": True}}))
        self.assertEqual(self.bash("echo hi > new.txt"), (0, {"block": False}))
        code, first = self.stop()
        self.assertTrue(first["block"])
        self.assertIn("ran none of the project's checks", first["reason"])
        self.assertEqual(self.stop(active=True), (0, {"block": False}))

    def test_the_bridge_caps_refused_stops_at_four(self):
        self.config.write_text(json.dumps({"discipline": {"check_before_stop": True, "max_rounds": 10}}))
        self.bash("echo hi > new.txt")
        self.assertEqual([self.stop(active=True)[1]["block"] for _ in range(6)], [True, True, True, True, False, False])

    def test_a_changed_test_carries_the_reminder_as_context(self):
        self.config.write_text(json.dumps({"discipline": {"reminders": True}}))
        code, answer = self.bash("echo x > test_new.py")
        self.assertEqual((code, answer["block"]), (0, False))
        self.assertIn("is a test", answer["context"])


class OmpPortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def spec(self, gate_config):
        for name in ("work", "state", "home"):
            (self.base / name).mkdir(exist_ok=True)
        config_file = self.base / "gate-config.json"
        if gate_config is not None:
            config_file.write_text(json.dumps(gate_config))
        return spec.RunSpec("omp/triage/f04-r0", "omp", "triage", self.base / "work", self.base / "state",
                            self.base / "home", "Tidy the logs.", 40123, "gpt-oss-120b", gate_config, config_file, 300,
                            {"PATTON_GATE_MISSION": "Tidy the logs."})

    def test_the_guarded_container(self):
        run_spec = self.spec({"triage": {"endpoint": "{model_base}/t1/v1"}})
        fleetkit.prepare_config(run_spec)
        argv = port.container_argv(run_spec, self.base / "sock", "n1")
        joined = " ".join(argv)
        self.assertIn("--network=none", argv)
        self.assertIn("patton-fleet=omp", argv)
        self.assertIn(fleetkit.IMAGE, argv)
        self.assertIn("-e /repo/lab/patton_lab/harness/omp/gate.ts", joined)
        self.assertIn("--approval-mode yolo", joined)
        self.assertIn("--model patton-fleet/gpt-oss-120b", joined)
        self.assertIn("PATTON_GATE_CONFIG=/patton-config/config.json", argv)
        self.assertIn("PATH=/opt/bun:/usr/local/bin:/usr/bin:/bin", argv)
        self.assertEqual(json.loads(run_spec.config_file.read_text())["triage"]["endpoint"], "http://127.0.0.1:8099/t1/v1")

    def test_the_unguarded_container_has_no_hook(self):
        argv = port.container_argv(self.spec(None), self.base / "sock", "n1")
        self.assertNotIn("gate.ts", " ".join(argv))
        self.assertNotIn("PATTON_GATE", " ".join(argv))

    def test_the_models_file_names_the_run_model_at_zero_cost(self):
        run_spec = self.spec({})
        port.prepare_home(run_spec)
        text = (run_spec.home / ".omp" / "agent" / "models.yml").read_text()
        self.assertIn("baseUrl: http://127.0.0.1:8099/v1", text)
        self.assertIn("- id: gpt-oss-120b", text)
        for key in ("input: 0", "output: 0", "cacheRead: 0", "cacheWrite: 0"):
            self.assertIn(key, text)

    def test_a_run_returns_the_last_assistant_text(self):
        events = json.dumps({"type": "message_end", "message": {"role": "assistant",
                                                                "content": [{"type": "text", "text": "Done."}]}})
        with mock.patch.object(fleetkit.subprocess, "run",
                               lambda argv, **k: subprocess.CompletedProcess(argv, 0, events + "\n", "")):
            result = port.run(self.spec({}))
        self.assertEqual((result.rc, result.timed_out, result.final_text), (0, False, "Done."))

    def test_the_port_contract(self):
        self.assertEqual(port.VERSION, "18.12.6")
        self.assertIs(port.STOP, True)
        self.assertIs(port.CONTEXT, True)
        self.assertEqual(port.shell_step("rm a"), {"tool": "bash", "args": {"command": "rm a"}})
        self.assertIs(spec.port("omp"), port)
        with mock.patch.object(port, "BUN", self.base / "missing"):
            self.assertIn("harness_cache/omp", port.available())


if __name__ == "__main__":
    unittest.main()
