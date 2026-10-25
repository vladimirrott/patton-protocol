"""The goose port without a container: the dialect's Stop and reminder handling through the real bridge and gate, the
stream-json reader, and the fleet port's run with the container launch stubbed."""

from pathlib import Path
from unittest import mock
import io
import json
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import goose as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.dialects import goose  # noqa: E402
from patton_lab.harness.goose import trace  # noqa: E402


class DialectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("only copy\n")
        self.env = {"HOME": str(self.base / "home"), "PATTON_GATE_ROOT": str(self.root),
                    "PATTON_GATE_STATE": str(self.base / "state"), "XDG_CONFIG_HOME": str(self.base / "config")}

    def configure(self, config):
        path = self.base / "gate.json"
        path.write_text(json.dumps(config))
        self.env["PATTON_GATE_CONFIG"] = str(path)

    def hook(self, payload):
        out = io.StringIO()
        code = bridge.main(["goose"], io.StringIO(json.dumps(payload)), out, self.env)
        return code, out.getvalue()

    def shell(self, command):
        return self.hook({"event": "PreToolUse", "session_id": "s1", "matcher_context": "shell", "tool_name": "shell",
                          "tool_input": {"command": command}, "working_dir": str(self.root), "tool_call_id": "c1"})

    def stop(self, final="Done."):
        return self.hook({"event": "Stop", "session_id": "s1", "matcher_context": None,
                          "last_assistant_message": final, "working_dir": str(self.root)})

    def test_the_stop_payload_becomes_a_stop_with_goose_s_last_message(self):
        self.assertEqual(goose.parse({"event": "Stop", "session_id": "s", "last_assistant_message": "bye",
                                      "working_dir": "/work"}),
                         {"event": "Stop", "final": "bye", "cwd": "/work", "session": "s"})
        # goose omits the field when the trailing text is empty
        self.assertEqual(goose.parse({"event": "Stop", "session_id": "s", "working_dir": "/work"})["final"], "")

    def test_an_unchecked_change_refuses_the_stop_in_goose_s_form_then_the_bridge_caps_it(self):
        self.configure({"discipline": {"check_before_stop": True, "max_rounds": 10}})
        self.assertEqual(self.shell(f"echo hi > {self.root}/new.txt"), (0, ""))
        answers = [self.stop() for _ in range(6)]
        blocked = [json.loads(out) for _, out in answers if out.strip()]
        self.assertEqual(len(blocked), 4)
        self.assertTrue(all(b["decision"] == "block" and trace.NO_CHECKS in b["reason"] for b in blocked))
        self.assertEqual(answers[4:], [(0, ""), (0, "")])

    def test_the_report_rule_reads_the_final_message(self):
        self.configure({"discipline": {"report": "all"}})
        self.assertEqual(self.stop(trace.REPORT), (0, ""))
        code, out = self.stop("Done.")
        self.assertIn("status", json.loads(out)["reason"])

    def test_emit_stop_and_emit_context(self):
        self.assertEqual(json.loads(goose.emit_stop("block", "why")[0]), {"decision": "block", "reason": "why"})
        self.assertEqual(goose.emit_stop("allow", ""), ("", 0))
        text, code = goose.emit_context("block", "Patton gate: no.", "Patton: a reminder.")
        self.assertEqual((code, json.loads(text)["decision"]), (0, "block"))
        self.assertIn("a reminder", json.loads(text)["reason"])
        self.assertEqual(goose.emit_context("allow", "", "Patton: lost."), ("", 0))  # goose has no channel for it

    def test_the_stream_reader_takes_the_last_text_and_skips_notifications(self):
        rows = ["    __( O)>  banner", json.dumps({"type": "message", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "first"}]}}),
            json.dumps({"type": "message", "message": {"role": "assistant", "content": [
                {"type": "systemNotification", "msg": "Stop hook blocked"}]}}),
            json.dumps({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "u"}]}}),
            json.dumps({"type": "message", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "last"}]}}), json.dumps({"type": "complete"})]
        self.assertEqual(trace.final_text("\n".join(rows)), "last")


class PortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        for d in ("work", "state", "home"):
            (self.base / d).mkdir()

    def spec(self, config):
        path = self.base / "gate-config.json"
        if config is not None:
            path.write_text(json.dumps(config))
        return spec.RunSpec("goose/stop/p02-r0", "goose", "stop", self.base / "work", self.base / "state",
                            self.base / "home", "Do it.", 4321, "gpt-oss-120b", config, path, 99,
                            {"PATTON_GATE_MISSION": "Do it."})

    def run_port(self, config):
        seen = {}

        def launch(name, work, state, home, plugin, tcp_port, prompt, timeout_s, model="scripted", config_file=None,
                   env=None, max_turns=8):
            seen.update(plugin=plugin, model=model, config_file=config_file, env=env, max_turns=max_turns,
                        hooks=json.loads((plugin / "hooks" / "hooks.json").read_text()) if plugin else None)
            line = json.dumps({"type": "message", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Finished."}]}})
            return {"code": 0, "stdout": line + "\n", "stderr": "", "seconds": 1.0}

        with mock.patch.object(trace, "launch", launch):
            result = port.run(self.spec(config))
        return result, seen

    def test_a_guarded_run_uses_the_shim_with_on_failure_block_and_the_model_base(self):
        config = {"verify": {"main": True, "endpoint": "{model_base}/t1/v1", "timeout_s": 300}}
        result, seen = self.run_port(config)
        self.assertEqual(result.final_text, "Finished.")
        self.assertEqual(json.loads(seen["config_file"].read_text())["verify"]["endpoint"],
                         "http://127.0.0.1:18080/t1/v1")
        pre = seen["hooks"]["hooks"]["PreToolUse"][0]["hooks"][0]
        self.assertEqual((pre["on_failure"], "patton-hook.sh goose" in pre["command"]), ("block", True))
        self.assertIn("patton-hook.sh goose stop", seen["hooks"]["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertEqual(seen["env"]["PATTON_HOOK_LIMIT"], str(45 + 300 + 300))
        self.assertGreater(pre["timeout"], 645)
        self.assertEqual((seen["model"], seen["max_turns"]), ("gpt-oss-120b", port.MAX_TURNS))
        self.assertEqual(seen["env"]["PATTON_GATE_MISSION"], "Do it.")

    def test_the_unguarded_run_mounts_no_plugin(self):
        _, seen = self.run_port(None)
        self.assertIsNone(seen["plugin"])
        self.assertIsNone(seen["config_file"])
        self.assertFalse((self.base / "home" / ".agents").exists())

    def test_the_contract(self):
        self.assertTrue(port.STOP)
        self.assertFalse(port.CONTEXT)
        self.assertEqual(port.VERSION, "1.56.0")
        self.assertEqual(port.shell_step("ls"), {"tool": "shell", "args": {"command": "ls"}})


if __name__ == "__main__":
    unittest.main()
