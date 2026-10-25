"""The dsh port without a container: the dialect's Stop and reminder handling through the real bridge and gate, the
PostToolUse reminder carrier, the session-log reader, and the fleet port's run with the container launch stubbed."""

from pathlib import Path
from unittest import mock
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import dsh as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.dialects import dsh  # noqa: E402
from patton_lab.harness.dsh import context_hook, trace  # noqa: E402

NODE = REPO / "lab" / "harness_cache" / "dsh" / "node" / "bin" / "node"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("only copy\n")
        self.state = self.base / "state"
        self.env = {"HOME": str(self.base / "home"), "PATTON_GATE_ROOT": str(self.root),
                    "PATTON_GATE_STATE": str(self.state), "XDG_CONFIG_HOME": str(self.base / "config"),
                    "PATH": os.environ.get("PATH", "")}

    def configure(self, config):
        path = self.base / "gate.json"
        path.write_text(json.dumps(config))
        self.env["PATTON_GATE_CONFIG"] = str(path)

    def hook(self, payload):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True):
            code = bridge.main(["dsh"], io.StringIO(json.dumps(payload)), out, self.env)
        return code, out.getvalue()

    def pre(self, tool, tool_input, call_id="c1"):
        return self.hook({"session_id": "s1", "transcript_path": "", "cwd": str(self.root),
                          "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                          "tool_use_id": call_id})

    def stop(self):
        return self.hook({"session_id": "s1", "transcript_path": "", "cwd": str(self.root), "hook_event_name": "Stop",
                          "stop_hook_active": False})


class DialectStopTests(Base):
    def test_a_stop_after_an_unchecked_change_is_refused_with_the_gates_reason_then_capped(self):
        self.configure({"discipline": {"check_before_stop": True, "max_rounds": 10}})
        self.assertEqual(self.pre("bash", {"description": "d", "command": "echo hi > new.txt"}), (0, ""))
        answers = [self.stop() for _ in range(6)]
        blocked = [json.loads(out) for code, out in answers if out.strip()]
        self.assertEqual(len(blocked), 4)  # the bridge's cap, as dsh has none
        self.assertTrue(all(b["decision"] == "block" and trace.NO_CHECKS in b["reason"] for b in blocked))
        self.assertEqual(answers[4:], [(0, ""), (0, "")])

    def test_a_stop_with_nothing_left_open_is_allowed(self):
        self.configure({"discipline": {"check_before_stop": True}})
        self.assertEqual(self.stop(), (0, ""))

    def test_the_stop_payload_becomes_a_stop_with_the_last_message_from_the_session_log(self):
        home = self.base / "dsh-home"
        log = home / "sessions" / "--work--" / "s1" / "session.v4.jsonl"
        log.parent.mkdir(parents=True)
        rows = [{"type": "assistant/message", "data": {"message": {"role": "assistant",
                                                                   "content": [{"type": "text", "text": "first"}]}}},
                {"type": "user/message", "data": {"message": {"role": "user", "content": [{"type": "text",
                                                                                           "text": "more"}]}}},
                {"type": "assistant/message", "data": {"message": {"role": "assistant", "content": [
                    {"type": "tool-call", "id": "x"}, {"type": "text", "text": "status: completed"}]}}}]
        log.write_text("".join(json.dumps(r) + "\n" for r in rows) + "{torn")
        with mock.patch.dict(os.environ, {"DSH_HOME": str(home)}):
            call = dsh.parse({"session_id": "s1", "cwd": "/work", "hook_event_name": "Stop", "stop_hook_active": False})
        self.assertEqual(call, {"event": "Stop", "final": "status: completed", "cwd": "/work", "session": "s1",
                                "active": False})

    def test_a_session_id_that_is_a_path_reads_nothing(self):
        with mock.patch.dict(os.environ, {"DSH_HOME": str(self.base)}):
            for session in ("../x", "*", "a/b", ".."):
                self.assertEqual(dsh.final_text(session), "")

    def test_emit_stop_is_claude_codes(self):
        self.assertEqual(json.loads(dsh.emit_stop("block", "why")[0]), {"decision": "block", "reason": "why"})
        self.assertEqual(dsh.emit_stop("allow", ""), ("", 0))

    @unittest.skipUnless(NODE.is_file() and shutil.which("zstd"), "needs the pinned Node and the zstd tool")
    def test_the_reader_decodes_every_frame_and_drops_a_torn_tail(self):
        frames = b""
        for text in ('{"n": 1}\n', '{"n": 2}\n', '{"n": 3}\n'):
            frames += subprocess.run(["zstd", "-q", "-c"], input=text.encode(), capture_output=True, check=True).stdout
        path = self.base / "session.v4.jsonl.zstd"
        path.write_bytes(frames + frames[: len(frames) // 3 // 2])
        done = subprocess.run([str(NODE), str(dsh.READER), str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([json.loads(line)["n"] for line in done.stdout.splitlines()], [1, 2, 3])


class DialectContextTests(Base):
    def test_an_allowed_calls_reminder_waits_for_the_post_tool_hook_which_prints_it_once(self):
        self.configure({"discipline": {"reminders": True}})
        payload = {"file_path": str(self.root / "tests" / "test_new.py"), "content": "x"}
        self.assertEqual(self.pre("write", payload, "call_7"), (0, ""))
        post = json.dumps({"hook_event_name": "PostToolUse", "tool_use_id": "call_7", "tool_name": "write"})
        out = io.StringIO()
        self.assertEqual(context_hook.main(io.StringIO(post), out, self.env), 0)
        context = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(context["hookEventName"], "PostToolUse")
        self.assertIn(trace.REMINDER, context["additionalContext"])
        again = io.StringIO()
        context_hook.main(io.StringIO(post), again, self.env)
        self.assertEqual(again.getvalue(), "")

    def test_a_refused_call_carries_the_reminder_in_its_reason(self):
        text, code = dsh.emit_context("block", "Patton gate: no.", "Patton: a reminder.")
        decision = json.loads(text)["hookSpecificOutput"]
        self.assertEqual((code, decision["permissionDecision"]), (0, "deny"))
        self.assertIn("a reminder", decision["permissionDecisionReason"])

    def test_the_carrier_and_the_dialect_agree_on_the_path(self):
        self.assertEqual(dsh.stash_path("/s", "call_1"), context_hook.stash_path("/s", "call_1"))

    def test_the_carrier_prints_nothing_on_garbage_or_a_missing_stash(self):
        for raw in ("{nope", "[]", json.dumps({"tool_use_id": "none-stashed"})):
            out = io.StringIO()
            self.assertEqual(context_hook.main(io.StringIO(raw), out, self.env), 0)
            self.assertEqual(out.getvalue(), "")


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
        return spec.RunSpec("dsh/rules/f04-r0", "dsh", "rules", self.base / "work", self.base / "state",
                            self.base / "home", "Do it.", 4321, "gpt-oss-120b", config, path, 99,
                            {"PATTON_GATE_MISSION": "Do it."})

    def run_port(self, config, stdout):
        seen = {}

        def launch(name, work, state, home, cfg, tcp_port, prompt, timeout_s, config_file=None, env=None):
            seen.update(cfg=cfg, tcp_port=tcp_port, prompt=prompt, timeout_s=timeout_s, config_file=config_file,
                        env=env, patch=(cfg / "patch.yml").read_text(),
                        hooks=(cfg / "hooks" / "hooks.json").read_text() if (cfg / "hooks" / "hooks.json").exists()
                        else None)
            return {"code": 0, "stdout": stdout, "stderr": "", "seconds": 1.0}

        with mock.patch.object(trace, "launch", launch):
            result = port.run(self.spec(config))
        return result, seen

    def test_a_guarded_run_mounts_the_shim_hooks_and_the_config_with_the_model_base(self):
        config = {"triage": {"on": True, "endpoint": "{model_base}/t1/v1", "timeout_s": 120}}
        stdout = '{"type":"text","text":"half"}\n{"type":"final","text":"All done."}\n'
        result, seen = self.run_port(config, stdout)
        self.assertEqual(result.final_text, "All done.")
        self.assertFalse(result.timed_out)
        self.assertEqual(json.loads(seen["config_file"].read_text())["triage"]["endpoint"],
                         "http://127.0.0.1:18080/t1/v1")
        self.assertIn("hooks-claude-code", seen["patch"])
        self.assertIn("id: gpt-oss-120b", seen["patch"])
        hooks = json.loads(seen["hooks"])["hooks"]
        self.assertIn("patton-hook.sh dsh", hooks["PreToolUse"][0]["hooks"][0]["command"])
        self.assertIn("patton-hook.sh dsh stop", hooks["Stop"][0]["hooks"][0]["command"])
        self.assertEqual(seen["env"]["PATTON_HOOK_LIMIT"], "165")  # 45 + the analyst's 120
        self.assertGreater(hooks["PreToolUse"][0]["hooks"][0]["timeout"], 165)
        self.assertEqual((seen["env"]["PATTON_GATE_MISSION"], seen["prompt"], seen["tcp_port"]), ("Do it.", "Do it.", 4321))
        self.assertFalse(seen["cfg"].exists())  # the per-run overlay is removed

    def test_the_unguarded_run_has_no_hook_at_all(self):
        result, seen = self.run_port(None, "")
        self.assertNotIn("hooks-claude-code", seen["patch"])
        self.assertIsNone(seen["hooks"])
        self.assertIsNone(seen["config_file"])
        self.assertEqual(result.final_text, "")

    def test_a_timeout_reads_as_one(self):
        with mock.patch.object(trace, "launch", lambda *a, **k: {"code": 124, "stdout": "", "stderr": "", "seconds": 9}):
            self.assertTrue(port.run(self.spec(None)).timed_out)

    def test_the_contract(self):
        self.assertTrue(port.STOP and port.CONTEXT)
        self.assertEqual(port.VERSION, "0.2.1-rc.3")
        self.assertEqual(port.shell_step("ls")["tool"], "bash")
        self.assertEqual(port.hook_limit({"verify": {"main": True, "timeout_s": 300}}), 45 + 300 + 300)
        self.assertEqual(port.hook_limit({}), 45)


if __name__ == "__main__":
    unittest.main()
