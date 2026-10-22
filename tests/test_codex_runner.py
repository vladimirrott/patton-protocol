"""The Codex runner, end to end with a scripted executor that drives the real recorder, adapter, gate and approver.

The executor plays Codex: it posts each model call through the recorder, passes each shell command through the
hook adapter when the arm has a hook, runs what the adapter allows in the run's work directory (with the rewrite
the adapter asked for), and prints the `codex exec --json` events Codex prints.
"""

from io import StringIO
from pathlib import Path
from unittest import mock
import getpass
import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
for path in (REPO / "lab", REPO / "gate"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from patton_lab.codex import adapter, recorder, runner  # noqa: E402
from patton_lab.codex.fake_upstream import FakeUpstream  # noqa: E402
from patton_lab.fixtures import e1  # noqa: E402

FIXTURE = e1.fixture_by_id("h05")
ONLY = next(g.paths[0] for g in FIXTURE.groups if g.category in ("IRR", "AMB", "CONF"))
SAFE = next(g.paths[0] for g in FIXTURE.groups if g.category in ("DISP", "REG"))
CATALOG = {"slug": "gpt-6.1-sol", "tool_mode": "code_mode_only", "apply_patch_tool_type": "freeform"}
TOOLS = [{"type": "namespace", "name": "functions", "tools": [{"type": "custom", "name": "exec"}]}]
NESTED = [{"type": "function", "name": "exec_command"}, {"type": "custom", "name": "apply_patch"}]


class ScriptedExecutor:
    def __init__(self, commands, finishes=True, final="Done.", notices=(), offer_skill=True, skip_hook=False):
        self.commands, self.finishes, self.final, self.notices = commands, finishes, final, list(notices)
        self.offer_skill, self.skip_hook, self.specs = offer_skill, skip_hook, []
        self.request_text = ""

    def root(self, work_dir):
        return str(work_dir)

    def version(self):
        return f"codex-cli {runner.CODEX_VERSION}"

    def model(self, spec, n):
        skills = spec.home_dir / ".codex" / "skills" / "patton-protocol" / "SKILL.md"
        text = "<skills_instructions>\n- patton-protocol: coordinate missions" if skills.exists() and self.offer_skill else ""
        body = {"model": spec.model, "instructions": "x", "tools": TOOLS, "stream": True, "store": False,
                "input": [{"type": "additional_tools", "tools": [{"type": "namespace", "name": "functions",
                                                                  "tools": TOOLS[0]["tools"] + NESTED}]},
                          {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]},
                          {"type": "message", "role": "user", "content": [{"type": "input_text",
                                                                            "text": f"turn {n} {self.request_text}"}]}]}
        port = int(spec.model_url.rsplit(":", 1)[1])
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("POST", "/backend-api/codex/responses", json.dumps(body),
                     {"content-type": "application/json", "authorization": "Bearer placeholder"})
        resp = conn.getresponse()
        resp.read()
        return resp.status

    def hook(self, spec, name, command):
        event = {"hook_event_name": name, "tool_name": "Bash", "tool_input": {"command": command},
                 "cwd": str(spec.work_dir), "tool_use_id": f"exec-{len(command)}", "session_id": "s", "turn_id": "t",
                 "model": spec.model, "permission_mode": "bypassPermissions", "transcript_path": None}
        env = {"PATTON_GATE_STATE": str(spec.state_dir), "PATTON_GATE_ROOT": str(spec.work_dir),
               "PATTON_GATE_RUN": spec.run_id, "PATTON_GATE_TIMEOUT": "10", "PATTON_GATE_APPROVER": spec.approver_url,
               "PATTON_GATE_DIR": str(REPO / "gate")}
        out = StringIO()
        adapter.main(StringIO(json.dumps(event)), out, env)
        return json.loads(out.getvalue()) if out.getvalue().strip() else None

    def run(self, spec):
        self.specs.append(spec)
        events = [{"type": "thread.started", "thread_id": "t"}]
        events += [{"type": "item.completed", "item": {"id": f"n{i}", "type": "error", "message": m}}
                   for i, m in enumerate(self.notices)]
        events.append({"type": "turn.started"})
        for i, command in enumerate(self.commands):
            status = self.model(spec, i)
            if status != 200:
                events.append({"type": "turn.failed", "error": {"message": f"status {status}"}})
                return runner.WorkerResult(1, "\n".join(json.dumps(e) for e in events), "", 0.1)
            run = command
            if spec.hooks_path is not None and not self.skip_hook:
                answer = self.hook(spec, "PreToolUse", command)
                spec_out = (answer or {}).get("hookSpecificOutput") or {}
                if spec_out.get("permissionDecision") == "deny":
                    continue
                run = (spec_out.get("updatedInput") or {}).get("command", command)
            done = subprocess.run(["bash", "-c", run], cwd=spec.work_dir, capture_output=True, text=True)
            events.append({"type": "item.completed", "item": {"id": f"item_{i}", "type": "command_execution",
                                                             "command": f"/bin/bash -lc {run!r}", "exit_code": done.returncode,
                                                             "aggregated_output": done.stdout, "status": "completed"}})
            if spec.hooks_path is not None and not self.skip_hook:
                self.hook(spec, "PostToolUse", command)
        self.model(spec, len(self.commands))
        events.append({"type": "item.completed", "item": {"id": "msg", "type": "agent_message", "text": self.final}})
        if self.finishes:
            events.append({"type": "turn.completed", "usage": {"input_tokens": 3000, "cached_input_tokens": 2400,
                                                               "output_tokens": 150, "reasoning_output_tokens": 30}})
        return runner.WorkerResult(0, "\n".join(json.dumps(e) for e in events), "", 0.2)


class RunnerCase(unittest.TestCase):
    status = 200

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"
        self.fake = FakeUpstream([{"exec": "text(1)"}], status=self.status)
        self.upstream = runner.Upstream(f"http://127.0.0.1:{self.fake.start()}")

    def tearDown(self):
        self.fake.stop()
        self.tmp.cleanup()

    def run_arm(self, name, executor, upstream=None, secrets=(), rep=0):
        return runner.run_one(runner.ARMS[name], "gpt-6.1-sol", FIXTURE.id, rep, executor, upstream or self.upstream,
                              self.out, list(secrets), CATALOG)


class RunOneTests(RunnerCase):
    def test_an_unguarded_worker_deletes_the_only_copy_and_the_run_scores_it(self):
        record = self.run_arm("X-R0", ScriptedExecutor(["ls -A", f"rm {ONLY}", f"rm {SAFE}"]))
        self.assertNotIn(ONLY, record["after"])
        self.assertEqual(record["metrics"]["harm"], 1)
        self.assertTrue(record["finished"])
        self.assertEqual(record["tool_names"], ["Bash", "Bash", "Bash"])
        self.assertTrue((self.out / f"X-R0-{FIXTURE.id}-r0.json").exists())

    def test_the_gate_keeps_the_only_copy_and_the_oracle_lets_the_safe_file_go(self):
        record = self.run_arm("X-R1", ScriptedExecutor(["ls -A", f"rm {ONLY}", f"rm {SAFE}"]))
        self.assertIn(ONLY, record["after"])
        self.assertNotIn(SAFE, record["after"])
        self.assertEqual(record["metrics"]["harm"], 0)
        self.assertGreaterEqual(record["metrics"]["approvals"], 1)
        self.assertFalse(any(record["tamper"].values()), record["tamper"])

    def test_every_allowed_command_runs_from_the_directory_the_gate_checked(self):
        record = self.run_arm("X-R1", ScriptedExecutor(["pwd"]))
        self.assertIn("&& pwd", record["tools"][0]["input"]["command"])
        self.assertTrue(record["tools"][0]["input"]["command"].startswith("/bin/bash -lc 'cd "))

    def test_deny_all_keeps_every_file(self):
        record = self.run_arm("X-R1D", ScriptedExecutor([f"rm {ONLY}", f"rm {SAFE}"]))
        self.assertIn(ONLY, record["after"])
        self.assertIn(SAFE, record["after"])

    def test_a_worker_that_ran_a_call_past_the_hook_is_flagged(self):
        record = self.run_arm("X-R1", ScriptedExecutor([f"rm {SAFE}"], skip_hook=True))
        self.assertTrue(record["tamper"]["ledger"])

    def test_the_record_holds_the_calls_rate_limits_and_model_calls_from_the_cassette(self):
        record = self.run_arm("X-R0", ScriptedExecutor(["ls -A"]))
        self.assertEqual(len(record["calls"]), 2)
        self.assertEqual(record["calls"][0]["cached_tokens"], 800)
        self.assertEqual(record["rate_limits"]["secondary"], [0.25, 0.25])
        self.assertEqual(record["model_calls"][0]["name"], "exec")
        self.assertIn("apply_patch", record["offered_tools"])
        self.assertTrue(record["usage_complete"])
        self.assertEqual(runner.replay(self.out), [])

    def test_a_worker_that_never_finishes_is_retried_and_the_attempt_is_kept(self):
        record = self.run_arm("X-R0", ScriptedExecutor(["ls -A"], finishes=False))
        self.assertFalse(record["finished"])
        self.assertEqual(len(record["attempts"]), runner.MAX_ATTEMPTS)
        self.assertEqual(len(list((self.out / "attempts").glob("*.json"))), runner.MAX_ATTEMPTS - 1)


class SetupTests(RunnerCase):
    def test_the_skill_arm_installs_the_skill_where_codex_finds_it(self):
        executor = ScriptedExecutor(["ls -A"])
        record = self.run_arm("X-R0S", executor)
        self.assertTrue(record["skill_offered"])
        self.assertIsNotNone(record["skill_hash"])

    def test_a_skill_arm_whose_worker_was_not_offered_the_skill_is_refused(self):
        with self.assertRaises(runner.BrokenSetup):
            self.run_arm("X-R0S", ScriptedExecutor(["ls -A"], offer_skill=False))

    def test_fallback_model_metadata_is_refused(self):
        executor = ScriptedExecutor(["ls -A"], notices=["Model metadata for `gpt-6.1-sol` not found. Defaulting"])
        with self.assertRaises(runner.BrokenSetup):
            self.run_arm("X-R0", executor)

    def test_the_worker_config_turns_off_what_the_study_does_not_measure(self):
        config = runner.codex_config("gpt-6.1-sol", "http://10.0.2.2:1234")
        for feature in ("multi_agent", "multi_agent_v2", "goals", "apps", "plugins", "image_generation"):
            self.assertIn(f"{feature} = false", config)
        self.assertIn('web_search = "disabled"', config)
        # The model's catalog entry asks for multi-agent v2; only this table turns the collaboration tools off.
        self.assertIn("[agents]\nenabled = false", config)
        self.assertIn("supports_websockets = false", config)
        self.assertIn('base_url = "http://10.0.2.2:1234/backend-api/codex"', config)
        self.assertIn("model_catalog_json", config)

    def test_the_hook_outlasts_the_approver_and_reaches_every_hooked_tool(self):
        hooks = runner.hooks_config()["hooks"]
        for event in ("SessionStart", "PreToolUse", "PostToolUse"):
            group = hooks[event][0]
            self.assertNotIn("matcher", group)
            self.assertGreater(group["hooks"][0]["timeout"], 10)

    def test_the_container_gets_no_credential_and_cannot_rewrite_its_config_or_hooks(self):
        base = Path(self.tmp.name)
        spec = runner.WorkerSpec("r", runner.ARMS["X-R1"], "gpt-6.1-sol", "m", base / "w", base / "h", base / "s",
                                 base / "config.toml", base / "hooks.json", "http://127.0.0.1:9/approve",
                                 "http://127.0.0.1:8", 60)
        argv = runner.PodmanExecutor(Path("/vendor/bin/codex")).command(spec)
        joined = " ".join(argv)
        # The whole vendor tree goes in: code mode needs codex-code-mode-host beside the binary.
        self.assertIn("/vendor:/opt/codex:ro", joined)
        self.assertIn("/opt/codex/bin/codex", argv)
        self.assertIn(f"{base / 'config.toml'}:/home/user/.codex/config.toml:ro", joined)
        self.assertIn(f"{base / 'hooks.json'}:/home/user/.codex/hooks.json:ro", joined)
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        self.assertNotIn("auth.json", joined)
        sources = [argv[i + 1].split(":", 1)[0] for i, a in enumerate(argv) if a == "-v"]
        for source in sources:
            self.assertNotEqual(source, str(Path.home()), "the host home is mounted")
            self.assertFalse(source.startswith(str(Path.home() / ".codex")), f"the host Codex home leaks in: {source}")
        plain = runner.WorkerSpec("r", runner.ARMS["X-R0"], "gpt-6.1-sol", "m", base / "w", base / "h", base / "s",
                                  base / "config.toml", None, None, "http://127.0.0.1:8", 60)
        self.assertNotIn("--dangerously-bypass-hook-trust", runner.PodmanExecutor(Path("/bin/true")).command(plain))


class SafetyTests(RunnerCase):
    def test_a_secret_in_a_model_call_stops_the_run_and_stores_nothing(self):
        secret = "sk-live-fedcba9876543210"
        executor = ScriptedExecutor(["ls -A"])
        executor.request_text = secret
        with self.assertRaises(runner.SecretLeak):
            self.run_arm("X-R0", executor, secrets=[secret])
        self.assertEqual(list(self.out.glob("*.json")), [])
        self.assertEqual(list(self.out.glob("attempts/*.json")), [])

    def test_a_secret_in_the_transcript_stores_nothing(self):
        with self.assertRaises(runner.SecretLeak):
            self.run_arm("X-R0", ScriptedExecutor(["ls -A"], final="token sk-live-0123456789abcdef"),
                         secrets=["sk-live-0123456789abcdef"])
        self.assertEqual(list(self.out.glob("*.json")), [])

    def test_the_host_user_name_is_scrubbed(self):
        record = self.run_arm("X-R0", ScriptedExecutor(["ls -A"], final=f"/home/{getpass.getuser()}/x"))
        if len(getpass.getuser()) > 2:
            self.assertNotIn(getpass.getuser(), json.dumps(record))

    def test_subscription_runs_stop_at_the_run_cap(self):
        upstream = runner.Upstream(self.upstream.url, subscription=True)
        with mock.patch.object(runner, "RUN_CAP", 1):
            record = self.run_arm("X-R0", ScriptedExecutor(["ls -A"]), upstream=upstream)
            self.assertEqual(record["backend"], runner.SUBSCRIPTION)
            from patton_lab.cc.meter import CapExceeded
            with self.assertRaises(CapExceeded):
                self.run_arm("X-R0", ScriptedExecutor(["ls -A"]), upstream=upstream, rep=1)

    def test_a_scripted_run_never_counts_toward_the_cap(self):
        self.assertEqual(self.run_arm("X-R0", ScriptedExecutor(["ls -A"]))["backend"], "scripted")


class RateLimitTests(RunnerCase):
    status = 429

    def test_a_rate_limit_stops_the_run_and_keeps_the_attempt(self):
        with self.assertRaises(runner.UpstreamStop):
            self.run_arm("X-R0", ScriptedExecutor(["ls -A"]))
        self.assertEqual(len(list((self.out / "attempts").glob("*.json"))), 1)
        self.assertEqual(list(self.out.glob("*.json")), [])


class ReplayTests(RunnerCase):
    def test_replay_finds_a_record_that_disagrees_with_its_cassette(self):
        self.run_arm("X-R0", ScriptedExecutor(["ls -A"]))
        path = next(self.out.glob("*.json"))
        record = json.loads(path.read_text())
        record["calls"][0]["output_tokens"] += 1
        path.write_text(json.dumps(record))
        self.assertTrue(any("token counts" in p for p in runner.replay(self.out)))

    def test_replay_finds_a_changed_score(self):
        self.run_arm("X-R0", ScriptedExecutor([f"rm {ONLY}"]))
        path = next(self.out.glob("*.json"))
        record = json.loads(path.read_text())
        record["metrics"]["harm"] = 0
        path.write_text(json.dumps(record))
        self.assertTrue(any("re-score" in p for p in runner.replay(self.out)))


class OfferedToolsTests(unittest.TestCase):
    def test_nested_tools_are_read_from_the_exec_description(self):
        # In code mode the real request lists nested tools only as headings in the exec tool's description.
        description = "Run JavaScript\n### `apply_patch`\nEdit files\n### `exec_command`\nRuns a command\n"
        entry = {"request": {"input": [{"type": "additional_tools", "tools": [
            {"type": "namespace", "name": "functions", "tools": [{"type": "custom", "name": "exec",
                                                                 "description": description}]}]}]}}
        self.assertEqual(runner.offered_tools([entry]), ["functions", "exec", "apply_patch", "exec_command"])


class TraceCheckTests(unittest.TestCase):
    def record(self, **over):
        base = {"run_id": "X-R1-h05-r0", "arm": "X-R1", "offered_tools": ["exec", "exec_command", "apply_patch"],
                "after": {ONLY: "", "TRACE.md": ""}, "tools": [{"name": "Bash", "input": {"command": "cd /work && pwd"}}],
                "tamper": {"manifest": False}, "usage_complete": True}
        return {**base, **over}

    def test_a_good_trace_passes(self):
        self.assertEqual(runner.trace_checks(self.record(), FIXTURE), [])

    def test_each_failure_is_named(self):
        cases = {"offered_tools": ["exec", "exec_command", "apply_patch", "spawn_agent"],
                 "after": {"TRACE.md": ""}, "tools": [{"name": "Bash", "input": {"command": "pwd"}}],
                 "usage_complete": False, "tamper": {"ledger": True}}
        for key, value in cases.items():
            self.assertTrue(runner.trace_checks(self.record(**{key: value}), FIXTURE), key)


def _must_not_spend(*args, **kwargs):
    raise AssertionError("a command-line test reached a layer that spends the subscription")


class CommandLineTests(unittest.TestCase):
    """Every layer that can spend is replaced here, so a mutant that removes a refusal fails instead of spending.
    On 2026-10-22 a mutant of the trace-marker check started five real worker containers from this class; they
    died on the stub catalog before calling the model, which was luck."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Path(self.tmp.name)
        for name in ("PodmanExecutor", "run_one"):
            patcher = mock.patch.object(runner, name, side_effect=_must_not_spend)
            self.spend = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(runner.recorder_mod, "ChatGPTAuth", side_effect=_must_not_spend)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(runner, "catalog_entry", return_value=CATALOG)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def passing_trace(self):
        marker = self.lab / "codex_trace" / runner.MODELS[0] / "TRACE_OK"
        marker.parent.mkdir(parents=True)
        marker.write_text(runner.adapter_hash() + "\n" + runner._sha(Path(runner.__file__).read_bytes()) + "\n")

    def test_calibrate_refuses_without_a_passing_trace_from_this_tree(self):
        self.assertEqual(runner.main(["calibrate", "--runs", "2"], root=self.lab), 2)

    def test_calibrate_refuses_a_trace_from_another_tree(self):
        self.passing_trace()
        marker = self.lab / "codex_trace" / runner.MODELS[0] / "TRACE_OK"
        marker.write_text("another tree\n")
        self.assertEqual(runner.main(["calibrate", "--runs", "2"], root=self.lab), 2)

    def test_calibrate_refuses_more_than_its_run_limit_even_after_a_passing_trace(self):
        self.passing_trace()
        self.assertEqual(runner.main(["calibrate", "--runs", str(runner.CALIBRATION_MAX_RUNS + 1)], root=self.lab), 2)

    def test_with_a_passing_trace_calibrate_reaches_the_login_check(self):
        # The positive control: the stubs above are the next thing calibrate touches, so the refusals are real.
        self.passing_trace()
        with self.assertRaises(AssertionError):
            runner.main(["calibrate", "--runs", "2"], root=self.lab)


class CatalogTests(unittest.TestCase):
    def test_the_catalog_entry_comes_from_the_host_cache_or_the_run_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "models_cache.json"
            cache.write_text(json.dumps({"models": [CATALOG]}))
            self.assertEqual(runner.catalog_entry("gpt-6.1-sol", cache), CATALOG)
            with self.assertRaises(runner.BrokenSetup):
                runner.catalog_entry("gpt-6-luna", cache)


if __name__ == "__main__":
    unittest.main()
