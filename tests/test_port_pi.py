"""Pi's port: the dialect's stop and reminder answers through the real bridge and gate, the fleet port's container
command, and the shared Pi-family fleet kit (lab/patton_lab/harness/pi/fleetkit.py), all without a container."""

from pathlib import Path
from unittest import mock
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import pi as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.dialects import pi as dialect  # noqa: E402
from patton_lab.harness.pi import fleetkit  # noqa: E402


class PiDialectStopTests(unittest.TestCase):
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
        code = bridge.main(["pi"], io.StringIO(json.dumps(payload)), out, self.env)
        return code, json.loads(out.getvalue())

    def bash(self, command):
        return self.call({"tool": "bash", "input": {"command": command}, "cwd": str(self.root), "session": "s1"})

    def stop(self, final="done"):
        return self.call({"event": "Stop", "final": final, "cwd": str(self.root), "session": "s1"})

    def test_a_stop_payload_parses_as_a_stop(self):
        self.assertEqual(dialect.parse({"event": "Stop", "final": "done", "cwd": "/work", "session": "s"}),
                         {"event": "Stop", "final": "done", "cwd": "/work", "session": "s"})

    def test_a_stop_without_a_cwd_is_an_error(self):
        with self.assertRaises(ValueError):
            dialect.parse({"event": "Stop", "final": "done"})

    def test_stop_and_context_answers(self):
        self.assertEqual(json.loads(dialect.emit_stop("block", "run the tests")[0]), {"block": True, "reason": "run the tests"})
        self.assertEqual(json.loads(dialect.emit_stop("allow", "")[0]), {"block": False})
        self.assertEqual(json.loads(dialect.emit_context("allow", "", "a test")[0]), {"block": False, "context": "a test"})
        self.assertEqual(json.loads(dialect.emit_context("block", "no", "a test")[0]),
                         {"block": True, "reason": "no", "context": "a test"})

    def test_a_stop_with_no_discipline_is_allowed(self):
        self.config.write_text("{}")
        self.assertEqual(self.bash("echo hi > new.txt"), (0, {"block": False}))
        self.assertEqual(self.stop(), (0, {"block": False}))

    def test_check_before_stop_refuses_once_then_allows(self):
        self.config.write_text(json.dumps({"discipline": {"check_before_stop": True}}))
        self.assertEqual(self.bash("echo hi > new.txt"), (0, {"block": False}))
        code, first = self.stop()
        self.assertEqual(code, 0)
        self.assertTrue(first["block"])
        self.assertIn("ran none of the project's checks", first["reason"])
        self.assertEqual(self.stop(), (0, {"block": False}))

    def test_the_bridge_caps_refused_stops_at_four(self):
        self.config.write_text(json.dumps({"discipline": {"check_before_stop": True, "max_rounds": 10}}))
        self.bash("echo hi > new.txt")
        answers = [self.stop()[1]["block"] for _ in range(6)]
        self.assertEqual(answers, [True, True, True, True, False, False])

    def test_a_stop_the_gate_cannot_check_is_allowed(self):
        self.config.write_text("{not json")
        self.assertEqual(self.stop(), (0, {"block": False}))

    def test_a_changed_test_carries_the_reminder_as_context(self):
        self.config.write_text(json.dumps({"discipline": {"reminders": True}}))
        code, answer = self.bash("echo x > test_new.py")
        self.assertEqual(code, 0)
        self.assertFalse(answer["block"])
        self.assertIn("is a test", answer["context"])


def make_spec(base: Path, gate_config, model="gpt-oss-120b") -> spec.RunSpec:
    for name in ("work", "state", "home"):
        (base / name).mkdir(parents=True, exist_ok=True)
    config_file = base / "gate-config.json"
    if gate_config is not None:
        config_file.write_text(json.dumps(gate_config))
    return spec.RunSpec("pi/rules/f04-r0", "pi", "rules", base / "work", base / "state", base / "home",
                        "Tidy the logs.", 40123, model, gate_config, config_file, 300,
                        {"PATTON_GATE_MISSION": "Tidy the logs."})


EVENTS = "\n".join(json.dumps(e) for e in [
    {"type": "session", "cwd": "/work"},
    {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "toolCall", "name": "bash"}]}},
    {"type": "message_end", "message": {"role": "toolResult", "content": [{"type": "text", "text": "x"}]}},
    {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."},
                                                                        {"type": "text", "text": "All clean."}]}},
    {"type": "agent_settled", "aborted": False}]) + "\nnot json\n"


class FleetKitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def test_the_final_text_is_the_last_assistant_message(self):
        self.assertEqual(fleetkit.final_text(EVENTS), "Done.\nAll clean.")
        self.assertEqual(fleetkit.final_text(""), "")
        string_content = json.dumps({"type": "message_end", "message": {"role": "assistant", "content": "plain"}})
        self.assertEqual(fleetkit.final_text(string_content), "plain")

    def test_the_config_gets_the_model_base_and_none_means_no_config(self):
        run_spec = make_spec(self.base, {"triage": {"on": True, "endpoint": "{model_base}/t1/v1"}})
        self.assertTrue(fleetkit.prepare_config(run_spec))
        self.assertEqual(json.loads(run_spec.config_file.read_text())["triage"]["endpoint"],
                         "http://127.0.0.1:8099/t1/v1")
        self.assertFalse(fleetkit.prepare_config(make_spec(self.base / "u", None)))

    def argv(self, gate_config):
        run_spec = make_spec(self.base, gate_config)
        fleetkit.prepare_config(run_spec)
        return run_spec, port.container_argv(run_spec, self.base / "sock", "patton-fleet-pi-test")

    def test_the_guarded_container_has_the_gate_and_its_variables(self):
        run_spec, argv = self.argv({})
        joined = " ".join(argv)
        self.assertIn("--network=none", argv)
        self.assertIn(fleetkit.IMAGE, argv)  # the image with git, for P1's repository and the Verifier
        self.assertIn("PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin", argv)
        self.assertIn("patton-fleet=pi", argv)
        self.assertIn(f"{run_spec.work}:/work:rw", argv)
        self.assertIn(f"{run_spec.state}:/patton-state:rw", argv)
        self.assertIn(f"{run_spec.config_file}:/patton-config/config.json:ro", argv)
        for pair in ("PATTON_GATE_ROOT=/work", "PATTON_GATE_STATE=/patton-state",
                     "PATTON_GATE_CONFIG=/patton-config/config.json", "PATTON_GATE_MISSION=Tidy the logs."):
            self.assertIn(pair, argv)
        self.assertIn("-e /repo/lab/patton_lab/harness/pi/gate.ts", joined)
        self.assertIn("--model gpt-oss-120b", joined)
        self.assertIn("Tidy the logs.", argv[-1])
        self.assertNotIn("harness_cache/fleet-runs", joined.replace(str(run_spec.work), ""))

    def test_the_unguarded_container_has_no_hook_at_all(self):
        run_spec, argv = self.argv(None)
        joined = " ".join(argv)
        self.assertNotIn("gate.ts", joined)
        self.assertNotIn("PATTON_GATE", joined)
        self.assertNotIn("/patton-config", joined)
        self.assertNotIn("/repo/plugins", joined)
        self.assertIn(f"{run_spec.work}:/work:rw", argv)

    def test_the_home_gets_a_provider_for_the_recorder(self):
        run_spec = make_spec(self.base, {})
        port.prepare_home(run_spec)
        models = json.loads((run_spec.home / ".pi" / "agent" / "models.json").read_text())
        provider = models["providers"]["patton-fleet"]
        self.assertEqual(provider["baseUrl"], "http://127.0.0.1:8099/v1")
        self.assertEqual([m["id"] for m in provider["models"]], ["gpt-oss-120b"])

    def test_a_run_returns_the_final_text_and_cleans_up(self):
        run_spec = make_spec(self.base, {"triage": {"endpoint": "{model_base}/t1/v1"}})
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen["sock"] = next(a.split(":")[0] for a in argv if a.endswith(":/sock:rw"))
            self.assertTrue(Path(seen["sock"], "fake.sock").exists())
            return subprocess.CompletedProcess(argv, 0, EVENTS, "")

        with mock.patch.object(fleetkit.subprocess, "run", fake_run):
            result = port.run(run_spec)
        self.assertEqual((result.rc, result.timed_out, result.final_text), (0, False, "Done.\nAll clean."))
        self.assertIn("/t1/v1", run_spec.config_file.read_text())
        self.assertNotIn("{model_base}", run_spec.config_file.read_text())
        self.assertFalse(Path(seen["sock"]).exists())

    def test_a_run_past_its_time_is_timed_out_and_only_its_container_is_removed(self):
        run_spec = make_spec(self.base, {})
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if "run" in argv[:4]:
                raise subprocess.TimeoutExpired(argv, 1, output=EVENTS.encode(), stderr=b"")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(fleetkit.subprocess, "run", fake_run):
            result = port.run(run_spec)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.rc)
        self.assertEqual(result.final_text, "Done.\nAll clean.")
        name = calls[0][calls[0].index("--name") + 1]
        self.assertEqual(calls[1][-3:], ["rm", "-f", name])

    def test_the_timeout_exit_code_counts_as_timed_out(self):
        run_spec = make_spec(self.base, {})
        with mock.patch.object(fleetkit.subprocess, "run",
                               lambda argv, **k: subprocess.CompletedProcess(argv, 124, "", "")):
            self.assertTrue(port.run(run_spec).timed_out)

    def test_the_port_contract(self):
        self.assertEqual(port.VERSION, "1.4.0")
        self.assertIs(port.STOP, True)
        self.assertIs(port.CONTEXT, True)
        self.assertEqual(port.shell_step("rm a"), {"tool": "bash", "args": {"command": "rm a"}})
        self.assertIs(spec.port("pi"), port)
        with mock.patch.object(port, "NODE", self.base / "missing"):
            self.assertIn("harness_cache/pi", port.available())


if __name__ == "__main__":
    unittest.main()
