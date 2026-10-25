"""The OpenCode fleet port and its reminder path, on the host: no container, no model, no network.

The dialect's `emit_context`, the bridge carrying the gate's test reminder through it, the plugin appending that
reminder to the tool's output (run under node with a stand-in bridge), and the pieces of `run` that decide what the
container gets: the config with and without the gate, the mounts, the variables, and the final text read from the
JSON event stream. The container itself is checked by `runner trace --harness opencode` and the harness's trace.
"""

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
from patton_lab.fleet.ports import opencode as port  # noqa: E402
from patton_lab.harness import bridge  # noqa: E402
from patton_lab.harness.dialects import opencode as dialect  # noqa: E402

PLUGIN = REPO / "lab" / "patton_lab" / "harness" / "opencode" / "plugin" / "patton-gate.js"


class DialectContextTests(unittest.TestCase):
    def test_an_allow_with_a_reminder_carries_it(self):
        text, code = dialect.emit_context("allow", "", "Patton: tests/test_x.py is a test.")
        self.assertEqual((json.loads(text), code), ({"allow": True, "context": "Patton: tests/test_x.py is a test."}, 0))

    def test_a_block_with_a_reminder_keeps_the_refusal(self):
        text, code = dialect.emit_context("block", "Patton gate: no.", "remember")
        self.assertEqual((json.loads(text), code), ({"allow": False, "reason": "Patton gate: no.", "context": "remember"},
                                                    0))

    def test_the_bridge_carries_the_test_reminder_for_a_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "work")
            (root / "tests").mkdir(parents=True)
            config = Path(tmp, "config.json")
            config.write_text(json.dumps({"discipline": {"reminders": True}, "files": {"protect": False}}))
            env = {"HOME": tmp, "PATTON_GATE_ROOT": str(root), "PATTON_GATE_STATE": str(Path(tmp, "s")),
                   "PATTON_GATE_CONFIG": str(config)}
            out = io.StringIO()
            code = bridge.main(["opencode"], io.StringIO(json.dumps({
                "tool": "write", "args": {"filePath": str(root / "tests" / "test_x.py"), "content": "x = 1\n"},
                "sessionID": "s", "directory": str(root)})), out, env)
            answer = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertIs(answer["allow"], True)
        self.assertIn("tests/test_x.py is a test", answer["context"])


@unittest.skipIf(shutil.which("node") is None, "node is not installed")
class PluginContextTests(unittest.TestCase):
    """The plugin under node, with a stand-in bridge that prints a fixed answer."""

    def hooks(self, answer: dict, script: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp, "bridge.py")
            fake.write_text(f"import sys; sys.stdin.read(); print({json.dumps(json.dumps(answer))})\n")
            runner = Path(tmp, "run.mjs")
            runner.write_text(f"import {{ PattonGate }} from {json.dumps(PLUGIN.as_uri())}\n"
                              "const hooks = await PattonGate({ directory: '/work' })\n" + script)
            env = {"PATH": os.environ["PATH"], "PATTON_BRIDGE_CMD": json.dumps([sys.executable, str(fake)])}
            done = subprocess.run(["node", str(runner)], capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    CALL = ("const input = { tool: 'write', sessionID: 's', callID: 'c1' }\n"
            "const output = { title: '', output: 'Wrote file successfully.', metadata: {} }\n")

    def test_an_allowed_call_gets_the_reminder_appended_to_its_output(self):
        out = self.hooks({"allow": True, "context": "Patton: tests/test_x.py is a test."}, self.CALL +
                         "await hooks['tool.execute.before'](input, { args: {} })\n"
                         "await hooks['tool.execute.after']({ ...input, args: {} }, output)\n"
                         "console.log(JSON.stringify(output.output))\n")
        text = json.loads(out)
        self.assertTrue(text.startswith("Wrote file successfully."), text)
        self.assertIn("Patton: tests/test_x.py is a test.", text)

    def test_a_reminder_is_used_once_and_only_for_its_own_call(self):
        out = self.hooks({"allow": True, "context": "REMINDER"}, self.CALL +
                         "await hooks['tool.execute.before'](input, { args: {} })\n"
                         "const other = { title: '', output: 'other', metadata: {} }\n"
                         "await hooks['tool.execute.after']({ ...input, callID: 'c2', args: {} }, other)\n"
                         "await hooks['tool.execute.after']({ ...input, args: {} }, output)\n"
                         "const again = { title: '', output: 'again', metadata: {} }\n"
                         "await hooks['tool.execute.after']({ ...input, args: {} }, again)\n"
                         "console.log(JSON.stringify([other.output, output.output.includes('REMINDER'), again.output]))\n")
        self.assertEqual(json.loads(out), ["other", True, "again"])

    def test_a_refused_call_throws_the_refusal_and_the_reminder(self):
        out = self.hooks({"allow": False, "reason": "Patton gate: no.", "context": "REMINDER"}, self.CALL +
                         "try { await hooks['tool.execute.before'](input, { args: {} }); console.log('ran') }\n"
                         "catch (err) { console.log(JSON.stringify(err.message)) }\n")
        message = json.loads(out)
        self.assertTrue(message.startswith("Patton gate: no."), message)
        self.assertIn("REMINDER", message)

    def test_an_allow_without_context_leaves_the_output_alone(self):
        out = self.hooks({"allow": True}, self.CALL +
                         "await hooks['tool.execute.before'](input, { args: {} })\n"
                         "await hooks['tool.execute.after']({ ...input, args: {} }, output)\n"
                         "console.log(JSON.stringify(output.output))\n")
        self.assertEqual(json.loads(out), "Wrote file successfully.")


class PortTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        for d in ("work", "state", "home"):
            (base / d).mkdir()
        self.base = base

    def spec(self, config):
        config_file = self.base / "gate-config.json"
        if config is not None:
            config_file.write_text(json.dumps(config))
        return spec.RunSpec("opencode/rules/f04-r0", "opencode", "rules", self.base / "work", self.base / "state",
                            self.base / "home", "Clean the logs.", 40123, "gpt-oss-120b", config, config_file, 77,
                            {"PATTON_GATE_MISSION": "Clean the logs."})

    def test_the_spec_names_this_module(self):
        self.assertIs(spec.port("opencode"), port)
        self.assertTrue(port.VERSION)
        self.assertEqual(port.shell_step("rm x")["tool"], "bash")
        self.assertEqual(port.shell_step("rm x")["args"]["command"], "rm x")

    def test_the_unguarded_arm_has_no_plugin_and_no_gate_variables(self):
        run_spec = self.spec(None)
        argv = port.podman_argv(run_spec, "pg-x", self.base / "relay")
        config = port.harness_config(run_spec)
        self.assertEqual(config["plugin"], [])
        self.assertFalse(any(a.startswith("PATTON_") for a in argv), argv)
        self.assertFalse(any("/patton-config" in a for a in argv), argv)

    def test_the_guarded_arm_mounts_the_config_with_the_model_base_filled_in(self):
        run_spec = self.spec({"triage": {"on": True, "endpoint": "{model_base}/t1/v1"}})
        port.prepare_config(run_spec)
        self.assertEqual(json.loads(run_spec.config_file.read_text())["triage"]["endpoint"],
                         f"http://127.0.0.1:{port.podrun.PORT_IN}/t1/v1")
        argv = port.podman_argv(run_spec, "pg-x", self.base / "relay")
        self.assertIn(f"{run_spec.config_file}:/patton-config/config.json:ro", argv)
        for pair in ("PATTON_GATE_ROOT=/work", "PATTON_GATE_STATE=/patton-state",
                     "PATTON_GATE_CONFIG=/patton-config/config.json", "PATTON_GATE_MISSION=Clean the logs."):
            self.assertIn(pair, argv)
        self.assertIn(f"{run_spec.work}:/work:rw", argv)
        self.assertIn(f"{run_spec.state}:/patton-state:rw", argv)
        self.assertEqual(port.harness_config(run_spec)["plugin"], [f"file://{port.PLUGIN_IN}"])

    def test_the_container_has_no_network_a_label_and_the_mission_as_its_prompt(self):
        run_spec = self.spec({})
        argv = port.podman_argv(run_spec, "pg-x", self.base / "relay")
        self.assertIn("--network=none", argv)
        self.assertIn("--label", argv)
        self.assertIn("patton-fleet=opencode", argv)
        self.assertEqual(argv[argv.index("--name") + 1], "pg-x")
        self.assertEqual(argv[-1], "Clean the logs.")
        self.assertIn("77", argv)  # the in-container timeout

    def test_the_harness_config_points_at_the_relay_with_the_runs_model_and_allows_everything(self):
        config = port.harness_config(self.spec({}))
        provider = config["provider"]["lab"]
        self.assertEqual(provider["options"]["baseURL"], f"http://127.0.0.1:{port.podrun.PORT_IN}/v1")
        self.assertEqual(list(provider["models"]), ["gpt-oss-120b"])
        self.assertEqual(config["model"], "lab/gpt-oss-120b")
        self.assertEqual(config["permission"], "allow")

    def test_the_contract_flags_say_no_stop_and_reminders_reach_the_model(self):
        self.assertIs(port.STOP, False)
        self.assertIs(port.CONTEXT, True)

    def test_git_is_mounted_and_on_path_and_opencode_keeps_no_snapshots(self):
        run_spec = self.spec(None)
        argv = port.podman_argv(run_spec, "pg-x", self.base / "relay")
        self.assertIn(f"{port.GIT_ROOT}:/opt/git/usr:ro", argv)
        self.assertIn(f"PATH={port.PATH_IN}", argv)
        self.assertTrue(port.PATH_IN.startswith("/opt/git/usr/bin:"))
        self.assertIn("GIT_EXEC_PATH=/opt/git/usr/lib/git-core", argv)
        self.assertIs(port.harness_config(run_spec)["snapshot"], False)

    @unittest.skipIf(port.available() is not None, "the OpenCode port cannot run here")
    def test_git_and_python3_run_in_the_ports_container(self):
        run_spec = self.spec(None)
        (run_spec.work / "tests").mkdir()
        (run_spec.work / "tests" / "__init__.py").write_text("")
        (run_spec.work / "tests" / "test_a.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test(self):\n        pass\n")
        name = f"pg-fleet-opencode-test-{os.getpid()}"
        argv = port.podman_argv(run_spec, name, self.base)
        argv = argv[:argv.index(port.podrun.IMAGE) + 1] + ["sh", "-c",
            "git init -q && git -c user.email=a@b -c user.name=a commit -q --allow-empty -m x && "
            "git log --oneline | wc -l && python3 -m unittest -q 2>&1 | tail -1"]
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=port.podrun._podman_env())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), ["1", "OK"])

    def test_the_final_text_is_the_last_text_part(self):
        stdout = "\n".join([json.dumps({"type": "text", "part": {"text": "first"}}), "not json",
                            json.dumps({"type": "tool_use", "part": {"tool": "bash"}}),
                            json.dumps({"type": "text", "part": {"text": "Done. I removed the logs I could."}})])
        self.assertEqual(port.final_text(stdout), "Done. I removed the logs I could.")
        self.assertEqual(port.final_text(""), "")

    def test_a_run_that_cannot_start_podman_removes_its_relay_and_reports(self):
        run_spec = self.spec({})
        made = []
        real = tempfile.mkdtemp

        def mkdtemp(*a, **k):
            made.append(real(*a, **k))
            return made[-1]
        with mock.patch.object(port.tempfile, "mkdtemp", mkdtemp), \
                mock.patch.object(port.subprocess, "run", side_effect=FileNotFoundError("podman")):
            with self.assertRaises(FileNotFoundError):
                port.run(run_spec)
        self.assertTrue(made)
        self.assertFalse(any(Path(m).exists() for m in made))


if __name__ == "__main__":
    unittest.main()
