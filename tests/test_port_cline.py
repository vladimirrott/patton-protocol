"""The Cline port on the host: the dialect's reminder answers through the real bridge and gate, and the fleet port's
hook file, config and container wiring (no container is started here; `runner trace` runs one)."""

from pathlib import Path
from unittest import mock
import io
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.harness import gitmount  # noqa: E402
from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import cline as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.cline import pod  # noqa: E402
from patton_lab.harness.dialects import cline as dialect  # noqa: E402


class DialectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("only copy\n")
        (self.root / "test_old.py").write_text("x = 1\n")
        (self.base / "config.json").write_text(json.dumps({"discipline": {"reminders": True}}))
        self.env = {"HOME": tmp.name, "PATTON_GATE_ROOT": str(self.root), "PATTON_GATE_STATE": str(self.base / "s"),
                    "PATTON_GATE_CONFIG": str(self.base / "config.json"), "PATTON_GATE_MISSION": "Add a test."}

    def call(self, tool, tool_input):
        payload = {"hookName": "tool_call", "taskId": "t1", "workspaceRoots": [str(self.root)],
                   "tool_call": {"id": "c1", "name": tool, "input": tool_input}}
        out = io.StringIO()
        code = bridge.main(["cline"], io.StringIO(json.dumps(payload)), out, self.env)
        return code, json.loads(out.getvalue())

    def test_a_reminder_rides_on_an_allow(self):
        code, answer = self.call("run_commands", {"commands": [f"echo 'x = 1' > {self.root}/test_new.py"]})
        self.assertEqual((code, answer["cancel"]), (0, False))
        self.assertIn("is a test", answer["context"])

    def test_a_reminder_rides_on_a_rewritten_refusal_and_the_command_never_runs(self):
        code, answer = self.call("run_commands", {"commands": [f"rm {self.root}/test_old.py"]})
        self.assertEqual(code, 0)
        self.assertEqual(len(answer["overrideInput"]["commands"]), 1)
        self.assertTrue(answer["overrideInput"]["commands"][0].endswith("; exit 1"))
        self.assertIn("Patton gate", answer["overrideInput"]["commands"][0])
        self.assertIn("is a test", answer["context"])

    def test_a_cancel_carries_no_context(self):
        dialect._LAST["tool"] = "editor"
        text, code = dialect.emit_context("block", "no", "Patton: a reminder")
        self.assertEqual((json.loads(text), code), ({"cancel": True, "errorMessage": "no"}, 0))


class PortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        for d in ("work", "state", "home"):
            (self.base / d).mkdir()
        # the git copy lives in the gitignored harness cache; these tests only build the container command
        patcher = mock.patch.object(gitmount, "why_not", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_spec(self, gate_config):
        config_file = self.base / "gate-config.json"
        if gate_config is not None:
            config_file.write_text(json.dumps(gate_config))
        return spec.RunSpec("r", "cline", "a", self.base / "work", self.base / "state", self.base / "home",
                            "Do it.", 4242, "gpt-oss-120b", gate_config, config_file, 300,
                            {"PATTON_GATE_MISSION": "Do it."})

    def test_the_contract_names(self):
        self.assertEqual(port.VERSION, "3.0.73")
        self.assertIs(port.STOP, False)
        self.assertIs(port.CONTEXT, True)
        self.assertEqual(port.shell_step("ls"), {"tool": "run_commands", "args": {"commands": ["ls"]}})

    def test_the_unguarded_arm_has_no_hook_and_no_gate_variables(self):
        run_spec = self.run_spec(None)
        port.prepare(run_spec)
        self.assertEqual(list((self.base / "home" / ".cline" / "hooks").iterdir()), [])
        mounts, env = port.mounts_env(run_spec)
        self.assertEqual(env, {})
        self.assertFalse([m for m in mounts if "/patton-config" in m[1]])

    def test_a_gated_arm_gets_the_shim_hook_and_the_filled_config(self):
        run_spec = self.run_spec({"triage": {"on": True, "endpoint": "{model_base}/t1/v1"}})
        port.prepare(run_spec)
        hook = self.base / "home" / ".cline" / "hooks" / "PreToolUse"
        self.assertIn('exec sh "$shim" cline', hook.read_text())
        self.assertTrue(hook.stat().st_mode & 0o100)
        self.assertEqual(json.loads(run_spec.config_file.read_text())["triage"]["endpoint"],
                         "http://127.0.0.1:18080/t1/v1")
        mounts, env = port.mounts_env(run_spec)
        self.assertIn((run_spec.config_file, "/patton-config/config.json:ro"), mounts)
        self.assertEqual(env["PATTON_GATE_STATE"], "/patton-state")
        self.assertEqual(env["PATTON_GATE_CONFIG"], "/patton-config/config.json")
        self.assertEqual(env["PATTON_GATE_MISSION"], "Do it.")
        self.assertEqual(env["PATTON_SHIM"], pod.SHIM)
        self.assertLess(int(env["PATTON_HOOK_LIMIT"]), 120)  # Cline's fixed hook timeout

    def test_the_container_has_no_network_a_label_and_the_task_flags(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"] = argv
            return mock.Mock(returncode=0, stdout='{"type": "run_result", "text": "finished"}\n', stderr="")
        with mock.patch.object(pod.subprocess, "run", fake_run):
            result = port.run(self.run_spec(None))
        argv = seen["argv"]
        self.assertIn("--network=none", argv)
        self.assertEqual(argv[argv.index("--label") + 1], "patton-fleet=cline")
        self.assertTrue(any(a.endswith(":/usr/local/bin/git:ro") for a in argv))
        script = argv[-1]
        self.assertIn("--auto-approve true", script)
        self.assertNotIn("--yolo", script)
        self.assertIn("-m gpt-oss-120b", script)
        self.assertEqual(result.final_text, "finished")

    def test_the_final_text_falls_back_to_the_last_text_block(self):
        lines = [{"type": "agent_event", "event": {"type": "content_end", "contentType": "text", "text": "a"}},
                 {"type": "agent_event", "event": {"type": "content_end", "contentType": "tool", "toolName": "x"}},
                 {"type": "agent_event", "event": {"type": "content_end", "contentType": "text", "text": "b"}}]
        result = pod.Result(0, False, 1.0, "\n".join(json.dumps(x) for x in lines), "")
        self.assertEqual(port.final_text(result), "b")


if __name__ == "__main__":
    unittest.main()
