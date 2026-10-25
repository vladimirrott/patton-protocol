"""Claude Code's fleet port: the container command for a guarded and an unguarded arm, the gate config's
{model_base}, the final text read from stream-json, the pinned binary check, and one run with podman replaced, all
without a container. The container itself is checked by `python3 -m patton_lab.fleet.runner trace --set p1
--harness claude-code`."""

from pathlib import Path
from unittest import mock
import json
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lab"))

from patton_lab import isolate  # noqa: E402
from patton_lab.fleet import spec  # noqa: E402
from patton_lab.fleet.ports import claude_code as port  # noqa: E402


def _flag_values(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


def _env(argv):
    return dict(v.split("=", 1) for v in _flag_values(argv, "-e"))


def _mounts(argv):
    return _flag_values(argv, "-v")


class ClaudeCodePortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        for d in ("work", "state", "home"):
            (self.base / d).mkdir()
        self.config_file = self.base / "gate-config.json"
        git = mock.patch.object(port.gitmount, "mounts", return_value=[])  # the git copy need not exist here
        git.start()
        self.addCleanup(git.stop)

    def make(self, config, env=None):
        if config is not None:
            self.config_file.write_text(json.dumps(config))
        return spec.RunSpec("claude-code/stop/p02-r0", "claude-code", "stop", self.base / "work", self.base / "state",
                            self.base / "home", "Fix the pager.\nThen stop.", 4242, "gpt-oss-120b", config,
                            self.config_file, 300, env or {"PATTON_GATE_MISSION": "Fix the pager.\nThen stop."})

    def test_contract_values(self):
        self.assertEqual(port.WIRE, "anthropic")
        self.assertIs(port.STOP, True)
        self.assertIs(port.CONTEXT, True)
        self.assertEqual(port.VERSION, "2.1.310")
        self.assertEqual(port.shell_step("echo hi"),
                         {"tool": "Bash", "input": {"command": "echo hi", "description": "run"}})

    def test_guarded_arm_loads_the_plugin_with_the_gate_environment(self):
        run_spec = self.make({"discipline": {"check_before_stop": True}})
        argv = port.podman_argv(run_spec, "pg-fleet-claude-code-x", self.base / "relay")
        env, mounts = _env(argv), _mounts(argv)
        self.assertEqual(argv[:3], ["podman", "--log-level", "error"])
        for flag in ("--network=none", "--cap-drop=all", "--read-only", "--rm", "--pull=never", "--userns=keep-id"):
            self.assertIn(flag, argv)
        self.assertNotIn("slirp4netns", " ".join(argv))
        self.assertIn("patton-fleet=claude-code", _flag_values(argv, "--label"))
        self.assertEqual(_flag_values(argv, "--name"), ["pg-fleet-claude-code-x"])
        self.assertEqual(env["PATTON_GATE_ROOT"], "/work")
        self.assertEqual(env["PATTON_GATE_STATE"], "/patton-state")
        self.assertEqual(env["PATTON_GATE_CONFIG"], "/patton-config/config.json")
        self.assertEqual(env["PATTON_GATE_MISSION"], "Fix the pager.\nThen stop.")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], port.MODEL_BASE)
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "placeholder")
        for key in ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                    "ANTHROPIC_SMALL_FAST_MODEL"):
            self.assertEqual(env[key], "gpt-oss-120b")
        self.assertEqual(env["CLAUDE_CODE_MODEL_CAPABILITIES"], "-mid_conv_system")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertIn(f"{self.config_file}:/patton-config/config.json:ro", mounts)
        self.assertIn(f"{port.PLUGIN}:{port.PLUGIN_IN}:ro", mounts)
        self.assertIn(f"{port.BINARY}:{port.BINARY_IN}:ro", mounts)
        self.assertIn(f"{self.base / 'work'}:/work:rw", mounts)
        self.assertIn(f"{self.base / 'state'}:/patton-state:rw", mounts)
        self.assertIn(f"{self.base / 'relay'}:/relay:ro", mounts)
        self.assertIn(isolate.IMAGE, argv)
        tail = argv[argv.index(isolate.IMAGE) + 1:]
        self.assertEqual(tail[:4], ["timeout", "-k", "5", "300"])
        self.assertIn("--plugin-dir", tail)
        self.assertEqual(_flag_values(tail, "--plugin-dir"), [port.PLUGIN_IN])
        self.assertEqual(_flag_values(tail, "--permission-mode"), ["bypassPermissions"])
        self.assertEqual(_flag_values(tail, "--output-format"), ["stream-json"])
        self.assertEqual(_flag_values(tail, "-p"), ["Fix the pager.\nThen stop."])
        self.assertEqual(_flag_values(tail, "--model"), ["gpt-oss-120b"])

    def test_unguarded_arm_has_no_plugin_no_gate_variable_and_no_config(self):
        argv = port.podman_argv(self.make(None), "n", self.base / "relay")
        joined = " ".join(argv)
        self.assertNotIn("--plugin-dir", argv)
        self.assertNotIn("patton-gate", joined)
        self.assertNotIn("/patton-config", joined)
        self.assertFalse([k for k in _env(argv) if k.startswith("PATTON_")])

    def test_git_is_mounted_for_the_p1_repository(self):
        with mock.patch.object(port.gitmount, "mounts", return_value=[(Path("/cache/git"), "/usr/local/bin/git:ro")]):
            argv = port.podman_argv(self.make(None), "n", self.base / "relay")
        self.assertIn("/cache/git:/usr/local/bin/git:ro", _mounts(argv))

    def test_prepare_config_fills_model_base(self):
        run_spec = self.make({"verify": {"main": True, "endpoint": "{model_base}/t1/v1"}})
        port.prepare_config(run_spec)
        self.assertEqual(json.loads(self.config_file.read_text())["verify"]["endpoint"], f"{port.MODEL_BASE}/t1/v1")

    def test_final_text_is_the_result_event_else_the_last_assistant_text(self):
        events = [{"type": "system", "subtype": "init"},
                  {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
                  {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
                  {"type": "assistant", "message": {"content": [{"type": "text", "text": "Finished."}]}}]
        stdout = "\n".join(json.dumps(e) for e in events) + "\nnot json\n"
        self.assertEqual(port.final_text(stdout), "Finished.")
        result = {"type": "result", "subtype": "success", "result": "All checks pass."}
        self.assertEqual(port.final_text(stdout + json.dumps(result) + "\n"), "All checks pass.")
        self.assertEqual(port.final_text(""), "")

    def test_available_refuses_a_missing_or_wrong_binary(self):
        missing = self.base / "nope"
        with mock.patch.object(port, "BINARY", missing):
            port._binary_ok.cache_clear()
            self.assertIn("lab/harness_cache/claude-code", port.available())
        wrong = self.base / "claude"
        wrong.write_bytes(b"not claude")
        with mock.patch.object(port, "BINARY", wrong):
            port._binary_ok.cache_clear()
            self.assertIn("not the pinned", port.available())
        port._binary_ok.cache_clear()

    def test_run_passes_the_argv_to_podman_and_reads_the_final_text(self):
        run_spec = self.make({"verify": {"endpoint": "{model_base}/t1/v1"}})
        seen = {}

        def fake_podman(argv, **kwargs):
            seen["argv"] = argv
            relay = Path([m for m in _mounts(argv) if m.endswith(":/relay:ro")][0].rsplit(":", 2)[0])
            seen["socket"] = (relay / "chat.sock").exists()
            out = json.dumps({"type": "result", "result": "Done."}) + "\n"
            return subprocess.CompletedProcess(argv, 0, out, "")

        with mock.patch.object(port.subprocess, "run", side_effect=fake_podman):
            result = port.run(run_spec)
        self.assertTrue(seen["socket"])
        self.assertEqual((result.rc, result.timed_out, result.final_text), (0, False, "Done."))
        self.assertIn(port.MODEL_BASE, self.config_file.read_text())

    def test_run_reports_a_timeout_and_removes_only_its_own_container(self):
        calls = []

        def fake_podman(argv, **kwargs):
            calls.append(argv)
            if "run" in argv[:4]:
                raise subprocess.TimeoutExpired(argv, 1, output=b"", stderr=b"")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(port.subprocess, "run", side_effect=fake_podman):
            result = port.run(self.make(None))
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.rc)
        name = _flag_values(calls[0], "--name")[0]
        self.assertTrue(name.startswith("pg-fleet-claude-code-"))
        self.assertEqual(calls[1][-1], name)
        self.assertIn("rm", calls[1])


if __name__ == "__main__":
    unittest.main()
