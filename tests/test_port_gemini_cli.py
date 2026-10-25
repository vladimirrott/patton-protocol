"""The fleet port of Gemini CLI: its dialect's stop and reminder paths, the AfterTool context hook, and the run's
settings and container command. The container runs themselves are `runner trace --set h1|p1 --harness gemini-cli`
and `harness/gemini-cli/trace_proxy.py`; this file needs no podman.
"""

from pathlib import Path
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import gemini_cli as port  # noqa: E402
from patton_lab.harness import bridge, fake_chat  # noqa: E402
from patton_lab.harness.dialects import gemini_cli as dialect  # noqa: E402

context_hook = port._load("patton_test_gemini_context_hook", port.HARNESS / "context_hook.py")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"PATTON_GEMINI_CONTEXT_DIR": str(self.base / "ctx")})
        patcher.start()
        self.addCleanup(patcher.stop)


class DialectStops(Tmp):
    def after_agent(self, **kw):
        return {"hook_event_name": "AfterAgent", "cwd": "/work", "session_id": "s9", "prompt": "Fix it.",
                "prompt_response": "All done.", "stop_hook_active": False, **kw}

    def test_after_agent_is_a_stop_with_the_response_as_final(self):
        self.assertEqual(dialect.parse(self.after_agent(stop_hook_active=True)),
                         {"event": "Stop", "final": "All done.", "cwd": "/work", "session": "s9", "active": True})

    def test_after_agent_without_an_absolute_cwd_is_an_error(self):
        with self.assertRaises(ValueError):
            dialect.parse(self.after_agent(cwd="work"))

    def test_emit_stop(self):
        self.assertEqual(dialect.emit_stop("allow", ""), ("", 0))
        text, code = dialect.emit_stop("block", "run the tests first")
        self.assertEqual((json.loads(text), code), ({"decision": "deny", "reason": "run the tests first"}, 0))

    def test_through_the_bridge_a_stop_after_an_unchecked_change_is_refused_once(self):
        root, state = self.base / "work", self.base / "state"
        root.mkdir()
        config = self.base / "config.json"
        config.write_text(json.dumps({"discipline": {"check_before_stop": True}}))
        env = {"HOME": str(self.base), "PATTON_GATE_ROOT": str(root), "PATTON_GATE_STATE": str(state),
               "PATTON_GATE_CONFIG": str(config), "XDG_CONFIG_HOME": str(self.base / "xdg")}

        def call(payload):
            out = io.StringIO()
            code = bridge.main(["gemini_cli"], io.StringIO(json.dumps(payload)), out, env)
            return code, out.getvalue()

        code, out = call({"hook_event_name": "BeforeTool", "tool_name": "run_shell_command", "cwd": str(root),
                          "session_id": "s1", "tool_input": {"command": "echo hi > new.txt"}})
        self.assertEqual((code, out), (0, ""))
        stop = self.after_agent(cwd=str(root), session_id="s1")
        code, out = call(stop)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["decision"], "deny")
        self.assertIn("ran none of the project's checks", json.loads(out)["reason"])
        self.assertEqual(call({**stop, "stop_hook_active": True}), (0, ""))


class DialectContext(Tmp):
    def before(self, command="echo '# x' >> tests/test_a.py"):
        return {"hook_event_name": "BeforeTool", "tool_name": "run_shell_command", "cwd": "/work",
                "session_id": "s1", "tool_input": {"command": command}}

    def test_a_refusal_carries_its_reminder_after_the_reason(self):
        dialect.parse(self.before())
        text, code = dialect.emit_context("block", "Patton gate: held.", "Patton: tests/test_a.py is a test.")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["reason"], "Patton gate: held.\n\nPatton: tests/test_a.py is a test.")
        self.assertEqual(dialect.take("run_shell_command", self.before()["tool_input"]), "")

    def test_an_allowed_call_keeps_its_reminder_for_after_tool_once(self):
        call = self.before()
        dialect.parse(call)
        self.assertEqual(dialect.emit_context("allow", "", "Patton: tests/test_a.py is a test."), ("", 0))
        self.assertEqual(dialect.take("run_shell_command", call["tool_input"]), "Patton: tests/test_a.py is a test.")
        self.assertEqual(dialect.take("run_shell_command", call["tool_input"]), "")

    def test_take_matches_by_input_and_falls_back_only_to_a_lone_reminder(self):
        a, b = {"command": "a"}, {"command": "b"}
        dialect.stash("run_shell_command", a, "for a")
        dialect.stash("run_shell_command", b, "for b")
        self.assertEqual(dialect.take("run_shell_command", b), "for b")
        self.assertEqual(dialect.take("run_shell_command", {"command": "a", "wait_for_previous": True}), "for a")
        dialect.stash("run_shell_command", a, "one")
        dialect.stash("run_shell_command", b, "two")
        self.assertEqual(dialect.take("run_shell_command", {"command": "c"}), "")

    def test_a_reminder_that_cannot_be_kept_never_blocks(self):
        Path(os.environ["PATTON_GEMINI_CONTEXT_DIR"]).write_text("a file where the directory should be")
        dialect.parse(self.before())
        self.assertEqual(dialect.emit_context("allow", "", "text"), ("", 0))

    def test_the_context_hook_answers_with_additional_context(self):
        call = self.before()
        dialect.stash("run_shell_command", call["tool_input"], "Patton: tests/test_a.py is a test.")
        out = io.StringIO()
        event = {"hook_event_name": "AfterTool", "tool_name": "run_shell_command", "tool_input": call["tool_input"],
                 "tool_response": {"llmContent": "ok"}}
        self.assertEqual(context_hook.main(io.StringIO(json.dumps(event)), out), 0)
        self.assertEqual(json.loads(out.getvalue()), {"hookSpecificOutput": {
            "hookEventName": "AfterTool", "additionalContext": "Patton: tests/test_a.py is a test."}})
        out = io.StringIO()
        self.assertEqual(context_hook.main(io.StringIO(json.dumps(event)), out), 0)
        self.assertEqual(out.getvalue(), "")

    def test_the_context_hook_says_nothing_on_garbage(self):
        out = io.StringIO()
        self.assertEqual(context_hook.main(io.StringIO("not json"), out), 0)
        self.assertEqual(out.getvalue(), "")


class Port(Tmp):
    def run_spec(self, config):
        work, state, home = (self.base / n for n in ("work", "state", "home"))
        for d in (work, state, home):
            d.mkdir(exist_ok=True)
        config_file = self.base / "gate-config.json"
        if config is not None:
            config_file.write_text(json.dumps(config))
        return spec.RunSpec("gemini-cli/stop/p02-r0", "gemini-cli", "stop", work, state, home, "Fix the pager.",
                            4242, "gpt-oss-120b", config, config_file, 300, {"PATTON_GATE_MISSION": "Fix the pager."})

    def test_the_contract_constants(self):
        self.assertEqual((port.VERSION, port.STOP, port.CONTEXT), ("0.65.0", True, True))
        self.assertIs(spec.port("gemini-cli"), port)

    def test_shell_step_becomes_the_clis_shell_call_through_the_proxy(self):
        step = port.shell_step("rm logs/audit.log")
        out = port.proxy_mod.GeminiProxy("http://127.0.0.1:1/v1", "m").gemini_response(fake_chat.completion(step, 0))
        (part,) = out["candidates"][0]["content"]["parts"]
        self.assertEqual(part["functionCall"], {"name": "run_shell_command", "args": {"command": "rm logs/audit.log"},
                                                "id": "call_0"})

    def test_no_hook_and_no_gate_without_a_gate_config(self):
        s = self.run_spec(None)
        self.assertFalse(port.prepare(s))
        settings = json.loads((s.home / ".gemini" / "settings.json").read_text())
        self.assertNotIn("hooks", settings)
        self.assertEqual(settings["security"]["auth"]["selectedType"], "gemini-api-key")
        argv = port.container_argv(s, self.base / "sock", "pf-gemini-cli-x", False)
        joined = " ".join(argv)
        self.assertNotIn("PATTON_GATE", joined)
        self.assertNotIn("/patton/", joined)
        self.assertNotIn(port.CONFIG_IN, joined)
        self.assertFalse(s.config_file.exists())

    def test_with_a_gate_the_hooks_go_through_the_shim(self):
        s = self.run_spec({"verify": {"endpoint": "{model_base}/t1/v1"}})
        self.assertTrue(port.prepare(s))
        self.assertEqual(json.loads(s.config_file.read_text())["verify"]["endpoint"], "http://127.0.0.1:18081/t1/v1")
        hooks = json.loads((s.home / ".gemini" / "settings.json").read_text())["hooks"]
        before, = hooks["BeforeTool"]
        self.assertEqual(before["hooks"][0]["command"], "sh /patton/lab/patton_lab/harness/shim/patton-hook.sh gemini_cli")
        self.assertGreater(before["hooks"][0]["timeout"], 45_000)  # over the shim's own limit
        stop = hooks["AfterAgent"][0]["hooks"][0]
        self.assertTrue(stop["command"].endswith("patton-hook.sh gemini_cli stop"))
        self.assertGreater(stop["timeout"], port.STOP_LIMIT_S * 1000)
        self.assertIn("context_hook.py", hooks["AfterTool"][0]["hooks"][0]["command"])
        self.assertEqual(before["matcher"], hooks["AfterTool"][0]["matcher"])

    def test_the_gated_container(self):
        s = self.run_spec({})
        port.prepare(s)
        argv = port.container_argv(s, self.base / "sock", "pf-gemini-cli-x", True)
        env = {a.split("=", 1)[0]: a.split("=", 1)[1] for a, prev in zip(argv[1:], argv) if prev == "-e"}
        mounts = [a for a, prev in zip(argv[1:], argv) if prev == "-v"]
        self.assertIn("--network=none", argv)
        self.assertEqual(argv[argv.index("--label") + 1], "patton-fleet=gemini-cli")
        self.assertEqual({k: env[k] for k in ("PATTON_GATE_ROOT", "PATTON_GATE_STATE", "PATTON_GATE_CONFIG",
                                               "PATTON_GATE_MISSION")},
                         {"PATTON_GATE_ROOT": "/work", "PATTON_GATE_STATE": "/patton-state",
                          "PATTON_GATE_CONFIG": "/patton-config/config.json", "PATTON_GATE_MISSION": "Fix the pager."})
        self.assertEqual(env["PATTON_RUN_PROMPT"], "Fix the pager.")
        self.assertEqual(env["GOOGLE_GEMINI_BASE_URL"], "http://127.0.0.1:18080")
        self.assertIn("/opt/git/usr/bin", env["PATH"])
        self.assertIn(f"{s.work}:/work:rw", mounts)
        self.assertIn(f"{s.state}:/patton-state:rw", mounts)
        self.assertIn(f"{s.config_file}:/patton-config/config.json:ro", mounts)
        self.assertTrue(all(m.endswith(":ro") for m in mounts if "/patton/" in m))
        self.assertIn("timeout -k 5 300 node", argv[-1])
        self.assertNotIn(str(Path.home()), json.dumps(env))

    def test_final_text_is_the_last_assistant_message(self):
        lines = [{"type": "init"}, {"type": "message", "role": "user", "content": "Fix it."},
                 {"type": "message", "role": "assistant", "content": "Look", "delta": True},
                 {"type": "message", "role": "assistant", "content": "ing.", "delta": True},
                 {"type": "tool_use", "tool_name": "run_shell_command"}, {"type": "tool_result"},
                 {"type": "message", "role": "assistant", "content": "All ", "delta": True},
                 {"type": "message", "role": "assistant", "content": "done.", "delta": True},
                 {"type": "result", "status": "success"}]
        out = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n"
        self.assertEqual(port.final_text(out), "All done.")
        self.assertEqual(port.final_text(""), "")


if __name__ == "__main__":
    unittest.main()
