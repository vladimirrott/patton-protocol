"""The Open Interpreter port on the host: its dialect's Stop and reminder answers through the real bridge and gate,
and the fleet port's hook, config and container wiring (no container is started here; `runner trace` runs one)."""

from pathlib import Path
import io
import json
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.harness import gitmount  # noqa: E402
from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import open_interpreter as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.dialects import open_interpreter as dialect  # noqa: E402


class DialectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("only copy\n")
        self.env = {"HOME": tmp.name, "PATTON_GATE_ROOT": str(self.root), "PATTON_GATE_STATE": str(self.base / "s"),
                    "PATTON_GATE_MISSION": "Add new.txt."}

    def config(self, cfg):
        (self.base / "config.json").write_text(json.dumps(cfg))
        self.env["PATTON_GATE_CONFIG"] = str(self.base / "config.json")

    def hook(self, payload):
        out = io.StringIO()
        code = bridge.main(["open_interpreter"], io.StringIO(json.dumps(payload)), out, self.env)
        text = out.getvalue().strip()
        return code, json.loads(text) if text else None

    def bash(self, command):
        return self.hook({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                          "cwd": str(self.root), "session_id": "s1", "tool_use_id": "c1"})

    def stop(self, message="done", active=False):
        return self.hook({"hook_event_name": "Stop", "cwd": str(self.root), "session_id": "s1",
                          "stop_hook_active": active, "last_assistant_message": message})

    def test_the_stop_payload_parses_with_the_last_message(self):
        self.assertEqual(dialect.parse({"hook_event_name": "Stop", "cwd": "/work", "session_id": "x",
                                        "stop_hook_active": True, "last_assistant_message": "all done"}),
                         {"event": "Stop", "final": "all done", "cwd": "/work", "session": "x", "active": True})

    def test_a_stop_after_an_unchecked_change_is_refused_once_then_allowed(self):
        self.config({"discipline": {"check_before_stop": True}})
        self.assertEqual(self.bash("echo fresh > new.txt"), (0, json.loads(json.dumps(self.bash_allow()))))
        code, answer = self.stop()
        self.assertEqual((code, answer["decision"]), (0, "block"))
        self.assertIn("ran none of the project's checks", answer["reason"])
        self.assertEqual(self.stop(active=True), (0, None))

    def bash_allow(self):
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": {"command": f"cd {self.root} && echo fresh > new.txt"}}}

    def test_a_stop_with_nothing_to_check_is_allowed(self):
        self.assertEqual(self.stop(), (0, None))

    def test_the_bridge_caps_refused_stops_at_four(self):
        self.config({"discipline": {"check_before_stop": True, "max_rounds": 10}})
        self.bash("echo fresh > new.txt")
        answers = [self.stop()[1] for _ in range(6)]
        self.assertEqual([bool(a) for a in answers], [True, True, True, True, False, False])

    def test_a_reminder_rides_on_the_allow_and_keeps_the_cd_rewrite(self):
        self.config({"discipline": {"reminders": True}})
        code, answer = self.bash("echo 'x = 1' > test_new.py")
        out = answer["hookSpecificOutput"]
        self.assertEqual((code, out["permissionDecision"]), (0, "allow"))
        self.assertEqual(out["updatedInput"]["command"], f"cd {self.root} && echo 'x = 1' > test_new.py")
        self.assertIn("is a test", out["additionalContext"])

    def test_a_reminder_next_to_a_deny_keeps_the_deny(self):
        (self.root / "test_old.py").write_text("x = 1\n")
        self.config({"discipline": {"reminders": True}})
        code, answer = self.bash("rm test_old.py")
        out = answer["hookSpecificOutput"]
        self.assertEqual((code, out["permissionDecision"]), (0, "deny"))
        self.assertIn("Patton gate", out["permissionDecisionReason"])
        self.assertIn("is a test", out["additionalContext"])
        self.assertNotIn("updatedInput", out)

    def test_emit_stop_allows_with_no_output(self):
        self.assertEqual(dialect.emit_stop("allow", ""), ("", 0))
        self.assertEqual(json.loads(dialect.emit_stop("block", "why")[0]), {"decision": "block", "reason": "why"})


class PortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        for d in ("work", "state", "home"):
            (base / d).mkdir()
        self.base = base
        # the git copy lives in the gitignored harness cache; these tests only build the container command
        patcher = mock.patch.object(gitmount, "why_not", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_spec(self, gate_config):
        config_file = self.base / "gate-config.json"
        if gate_config is not None:
            config_file.write_text(json.dumps(gate_config))
        return spec.RunSpec("r", "open-interpreter", "a", self.base / "work", self.base / "state", self.base / "home",
                            "Do it.", 4242, "gpt-oss-120b", gate_config, config_file, 300,
                            {"PATTON_GATE_MISSION": "Do it."})

    def test_the_contract_names(self):
        self.assertEqual(port.VERSION, "0.0.56")
        self.assertIs(port.STOP, True)
        self.assertIs(port.CONTEXT, True)
        self.assertEqual(port.shell_step("ls"), {"tool": "exec_command", "args": {"cmd": "ls"}})

    def test_the_unguarded_arm_has_no_hook_no_gate_variables_and_no_config(self):
        run_spec = self.run_spec(None)
        port.prepare(run_spec)
        hooks = json.loads((self.base / "home" / ".openinterpreter" / "hooks.json").read_text())
        self.assertEqual(hooks, {"hooks": {}})
        argv = port.command(run_spec, "c", "/tmp/x")
        self.assertFalse([a for a in argv if a.startswith("PATTON_GATE")])
        self.assertFalse([a for a in argv if "/patton-config" in a])

    def test_a_gated_arm_runs_the_shim_for_tools_and_stops_with_limits_under_the_hook_timeouts(self):
        run_spec = self.run_spec({"triage": {"on": True, "endpoint": "{model_base}/t1/v1"}})
        port.prepare(run_spec)
        hooks = json.loads((self.base / "home" / ".openinterpreter" / "hooks.json").read_text())["hooks"]
        pre, stop = hooks["PreToolUse"][0]["hooks"][0], hooks["Stop"][0]["hooks"][0]
        self.assertTrue(pre["command"].endswith(f"sh {port.SHIM} open_interpreter"))
        self.assertTrue(stop["command"].endswith(f"sh {port.SHIM} open_interpreter stop"))
        self.assertLess(port.TOOL_LIMIT_S, pre["timeout"])
        self.assertLess(port.STOP_LIMIT_S, stop["timeout"])
        self.assertGreater(port.TOOL_LIMIT_S, 120)  # T1's own timeout in the h1 suite
        self.assertGreater(port.STOP_LIMIT_S, 300)  # the Verifier's in the p1 suite
        self.assertIn(f"PATTON_HOOK_LIMIT={port.TOOL_LIMIT_S} ", pre["command"])
        self.assertEqual(json.loads(run_spec.config_file.read_text())["triage"]["endpoint"],
                         "http://127.0.0.1:18080/t1/v1")
        argv = port.command(run_spec, "c", "/tmp/x")
        for want in ("PATTON_GATE_ROOT=/work", "PATTON_GATE_STATE=/patton-state",
                     "PATTON_GATE_CONFIG=/patton-config/config.json", "PATTON_GATE_MISSION=Do it.",
                     f"{run_spec.config_file}:/patton-config/config.json:ro"):
            self.assertIn(want, argv)

    def test_the_container_has_no_network_a_label_and_git(self):
        argv = port.command(self.run_spec(None), "c", "/tmp/x")
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertEqual(argv[argv.index("--label") + 1], "patton-fleet=oi")
        self.assertTrue(any(a.endswith(":/usr/local/bin/git:ro") for a in argv))

    def test_the_model_is_the_runs_in_the_provider_and_the_catalog(self):
        port.prepare(self.run_spec(None))
        home = self.base / "home" / ".openinterpreter"
        self.assertIn('model = "gpt-oss-120b"', (home / "config.toml").read_text())
        self.assertIn('wire_api = "chat"', (home / "config.toml").read_text())
        self.assertEqual(json.loads((home / "catalog.json").read_text())["models"][0]["slug"], "gpt-oss-120b")

    def test_the_final_text_is_the_last_agent_message(self):
        lines = [{"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
                 {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "x"}},
                 {"type": "item.completed", "item": {"type": "agent_message", "text": "last"}},
                 {"type": "turn.completed"}]
        self.assertEqual(port.final_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n"), "last")
        self.assertEqual(port.final_text(""), "")


if __name__ == "__main__":
    unittest.main()
