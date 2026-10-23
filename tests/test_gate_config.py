"""The patton-gate plugin's configuration: layers, the tighten-only rule for project files, and every file setting.

The configuration is read when a session starts and copied into the session's state, so these tests write the
files, start a session, then send tool calls.
"""

from io import StringIO
from pathlib import Path
import json
import os
import time
import unittest

from test_gate_plugin import GateCase, gate


class ConfigCase(GateCase):
    def setUp(self):
        super().setUp()
        self.env["XDG_CONFIG_HOME"] = str(self.home / "config")

    def user_config(self, data):
        path = self.home / "config" / "patton-gate" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def project_config(self, data):
        path = self.root / ".claude" / "patton-gate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def write(self, file_path, tool_use_id="w1", mode="default"):
        return self.hook({"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_use_id": tool_use_id,
                          "tool_input": {"file_path": str(file_path), "content": "x"}, "permission_mode": mode})

    def reason(self, result):
        return result[1]["hookSpecificOutput"]["permissionDecisionReason"]

    def config_cli(self, *args):
        out = StringIO()
        rc = gate.config_command(list(args), out, {**self.env})
        return rc, out.getvalue()


class LayerTests(ConfigCase):
    def test_without_any_file_the_defaults_are_the_studied_behaviour(self):
        config, sources, ignored = gate.load_config(str(self.root), {**self.env})
        self.assertEqual(config["files"]["on_change"], "ask")
        self.assertEqual(config["files"]["max_refusals"], 5)
        self.assertEqual(config["missions"]["max_workers"], None)
        self.assertEqual(set(sources.values()), {"default"})
        self.assertEqual(ignored, [])

    def test_the_user_file_sets_a_value_and_the_gate_uses_it(self):
        self.user_config({"files": {"on_change": "deny"}})
        self.start()
        result = self.bash("rm notes.txt")
        self.assertEqual(self.decision(result), "deny")
        self.assertIn("notes.txt", self.reason(result))

    def test_a_project_file_can_tighten(self):
        self.project_config({"files": {"on_change": "deny"}})
        self.start()
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "deny")

    def test_a_project_file_cannot_loosen_and_the_ignored_field_is_named(self):
        self.user_config({"files": {"on_change": "deny"}})
        self.project_config({"files": {"on_change": "ask", "protect": False, "ignore": ["*.txt"],
                                       "max_refusals": 50, "trusted_commands": ["rm"]}})
        config, _sources, ignored = gate.load_config(str(self.root), {**self.env})
        self.assertEqual(config["files"]["on_change"], "deny")
        self.assertTrue(config["files"]["protect"])
        self.assertEqual(config["files"]["ignore"], [])
        self.assertEqual(config["files"]["max_refusals"], 5)
        self.assertEqual(config["files"]["trusted_commands"], [])
        self.assertEqual(sorted(i["field"] for i in ignored),
                         ["files.ignore", "files.max_refusals", "files.on_change", "files.protect",
                          "files.trusted_commands"])
        self.start()
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "deny")
        _rc, text = self.config_cli()
        self.assertIn("ignored", text)
        self.assertIn("files.protect", text)

    def test_a_project_file_may_lower_a_limit_turn_a_check_on_and_add_protected_patterns(self):
        self.user_config({"missions": {"max_workers": 4}})
        self.project_config({"missions": {"max_workers": 2, "exclusive_paths": True, "max_commands": 30},
                             "files": {"always_protect": ["*.env"], "unreadable_commands": "deny"}})
        config, _sources, ignored = gate.load_config(str(self.root), {**self.env})
        self.assertEqual(config["missions"]["max_workers"], 2)
        self.assertTrue(config["missions"]["exclusive_paths"])
        self.assertEqual(config["missions"]["max_commands"], 30)  # off before, so a number is tighter
        self.assertEqual(config["files"]["always_protect"], ["*.env"])
        self.assertEqual(config["files"]["unreadable_commands"], "deny")
        self.assertEqual(ignored, [])

    def test_a_project_file_cannot_raise_a_limit_the_user_set(self):
        self.user_config({"missions": {"max_workers": 2, "max_commands": 10}})
        self.project_config({"missions": {"max_workers": 8, "max_commands": None}})
        config, _sources, ignored = gate.load_config(str(self.root), {**self.env})
        self.assertEqual((config["missions"]["max_workers"], config["missions"]["max_commands"]), (2, 10))
        self.assertEqual(sorted(i["field"] for i in ignored), ["missions.max_commands", "missions.max_workers"])

    def test_the_users_projects_map_loosens_one_project(self):
        self.user_config({"files": {"on_change": "deny"},
                          "projects": {str(self.root): {"files": {"on_change": "ask", "ignore": ["*.txt"]}}}})
        config, sources, _ignored = gate.load_config(str(self.root), {**self.env})
        self.assertEqual(config["files"]["on_change"], "ask")
        self.assertEqual(sources["files.ignore"], f"user file, projects[{self.root}]")
        self.start()
        self.assertIsNone(self.decision(self.bash("rm notes.txt")))

    def test_the_explicit_config_variable_replaces_the_user_file(self):
        self.user_config({"files": {"on_change": "deny"}})
        other = Path(self.tmp.name) / "other.json"
        other.write_text(json.dumps({"files": {"max_refusals": 2}}))
        config, _s, _i = gate.load_config(str(self.root), {**self.env, "PATTON_GATE_CONFIG": str(other)})
        self.assertEqual((config["files"]["on_change"], config["files"]["max_refusals"]), ("ask", 2))

    def test_the_patton_preset_sets_the_protocol_numbers_and_a_field_beside_it_wins(self):
        self.user_config({"missions": {"preset": "patton", "max_commands": 40}})
        config, _s, _i = gate.load_config(str(self.root), {**self.env})
        m = config["missions"]
        self.assertEqual((m["max_workers"], m["max_commands"], m["timeout_minutes"], m["max_retries"],
                          m["exclusive_paths"], m["max_output_tokens"]), (4, 40, 15, 2, True, None))

    def test_a_project_preset_turns_the_limits_on_but_never_raises_them(self):
        self.user_config({"missions": {"max_workers": 2}})
        self.project_config({"missions": {"preset": "patton"}})
        config, _s, _i = gate.load_config(str(self.root), {**self.env})
        self.assertEqual((config["missions"]["max_workers"], config["missions"]["max_commands"]), (2, 20))


class InvalidConfigTests(ConfigCase):
    def assert_refused_naming(self, path, words):
        self.start()
        result = self.bash("ls")
        self.assertEqual(self.decision(result), "deny")
        reason = self.reason(result)
        self.assertIn(str(path), reason)
        for word in words:
            self.assertIn(word, reason)

    def test_unreadable_json_refuses_every_call_and_names_the_file(self):
        self.assert_refused_naming(self.user_config("{not json"), ["configuration"])

    def test_an_unknown_field_refuses_and_names_it(self):
        self.assert_refused_naming(self.user_config({"files": {"on_chnage": "deny"}}), ["files.on_chnage"])

    def test_a_wrong_type_refuses_and_names_it(self):
        self.assert_refused_naming(self.user_config({"missions": {"max_workers": "four"}}), ["missions.max_workers"])

    def test_a_negative_limit_refuses(self):
        self.assert_refused_naming(self.user_config({"missions": {"max_commands": -1}}), ["missions.max_commands"])

    def test_a_bad_choice_refuses(self):
        self.assert_refused_naming(self.user_config({"files": {"on_change": "maybe"}}), ["files.on_change"])

    def test_a_bad_project_file_refuses_too(self):
        self.assert_refused_naming(self.project_config("[1, 2]"), ["configuration"])

    def test_check_reports_an_error_without_a_session(self):
        path = self.user_config({"files": {"bogus": 1}})
        rc, text = self.config_cli("--check", str(path))
        self.assertEqual(rc, 1)
        self.assertIn("files.bogus", text)
        good = self.user_config({"files": {"on_change": "deny"}})
        rc, text = self.config_cli("--check", str(good))
        self.assertEqual(rc, 0, text)


class SnapshotTests(ConfigCase):
    def test_an_edit_during_the_session_waits_for_the_next_session(self):
        self.start()
        self.user_config({"files": {"on_change": "deny"}})
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")
        self.session = "sess-2"
        self.start()
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "deny")

    def test_a_resumed_session_keeps_its_first_configuration(self):
        self.start()
        self.user_config({"files": {"on_change": "deny"}})
        self.start(source="resume")
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")

    def test_a_session_from_before_configuration_existed_uses_the_defaults(self):
        self.start()
        (self.state_dir() / "config.json").unlink()
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")


class FileSettingTests(ConfigCase):
    def test_protect_off_leaves_file_changes_to_the_persons_settings(self):
        self.user_config({"files": {"protect": False}})
        self.start()
        self.assertIsNone(self.decision(self.bash("rm notes.txt")))
        self.assertIsNone(self.decision(self.bash("python3 -c 'print(1)'")))

    def test_unreadable_commands_can_be_refused_or_left_alone(self):
        self.user_config({"files": {"unreadable_commands": "deny"}})
        self.start()
        self.assertEqual(self.decision(self.bash("python3 -c 'print(1)'")), "deny")
        self.user_config({"files": {"unreadable_commands": "allow"}})
        self.session = "sess-2"
        self.start()
        self.assertIsNone(self.decision(self.bash("python3 -c 'print(1)'")))
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")  # a command it can read still asks

    def test_a_trusted_command_runs_without_a_question(self):
        self.user_config({"files": {"trusted_commands": ["pytest", "make test"]}})
        self.start()
        self.assertIsNone(self.decision(self.bash("pytest -q tests/")))
        self.assertIsNone(self.decision(self.bash("make test")))

    def test_trust_never_covers_a_second_command_a_redirect_or_an_expansion(self):
        self.user_config({"files": {"trusted_commands": ["pytest", "make test"]}})
        self.start()
        for command in ("pytest; rm notes.txt", "pytest && rm notes.txt", "pytest > notes.txt", "pytest $(rm x)",
                        "make test-all", "make", "pytestx", "pytest | sh", "pytest\nrm notes.txt",
                        'pytest "$(rm notes.txt)"', "pytest $TARGET", "pytest {a,b}"):
            with self.subTest(command=command):
                self.assertEqual(self.decision(self.bash(command, tool_use_id=command)), "ask")

    def test_an_ignored_pattern_is_not_protected(self):
        (self.root / "build.log").write_text("x")
        (self.root / "logs" / "deep").mkdir(parents=True)
        (self.root / "logs" / "deep" / "run.log").write_text("x")
        (self.root / "src" / "keep.log.txt").write_text("x")
        self.user_config({"files": {"ignore": ["*.log", "src/**"]}})
        self.start()
        self.assertIsNone(self.decision(self.bash("rm build.log")))
        self.assertIsNone(self.decision(self.bash("rm logs/deep/run.log", "deep")))  # no slash: any depth
        self.assertIsNone(self.decision(self.bash("rm src/app.py")))
        self.assertEqual(self.decision(self.bash("rm notes.txt")), "ask")

    def test_an_anchored_pattern_matches_only_from_the_project_root(self):
        (self.root / "a" / "src").mkdir(parents=True)
        (self.root / "a" / "src" / "x.py").write_text("x")
        self.user_config({"files": {"ignore": ["src/*.py"]}})
        self.start()
        self.assertIsNone(self.decision(self.bash("rm src/app.py")))
        self.assertEqual(self.decision(self.bash("rm a/src/x.py", "nested")), "ask")

    def test_an_always_protected_pattern_covers_files_created_later(self):
        self.project_config({"files": {"always_protect": ["*.env", "secrets/**"]}})
        self.start()
        (self.root / "late.env").write_text("x")
        self.assertEqual(self.decision(self.bash("rm late.env")), "ask")
        self.assertEqual(self.decision(self.write(self.root / "secrets" / "new.key")), "ask")
        self.assertIsNone(self.decision(self.write(self.root / "other.txt")))

    def test_skip_dirs_replaces_the_list_of_rebuilt_directories(self):
        (self.root / "build").mkdir()
        (self.root / "build" / "out.o").write_text("x")
        self.user_config({"files": {"skip_dirs": ["build"]}})
        self.start()
        self.assertIsNone(self.decision(self.bash("rm build/out.o")))

    def test_max_refusals_sets_when_the_mission_is_told_to_stop(self):
        self.user_config({"files": {"on_change": "deny", "max_refusals": 2}})
        self.start()
        self.bash("rm notes.txt", "a")
        self.bash("rm notes.txt", "b")
        result = self.bash("rm src/app.py", "c")
        self.assertIn("refused too many times", self.reason(result))


class OwnFilesTests(ConfigCase):
    def test_a_change_to_the_gates_configuration_always_asks(self):
        user = self.user_config({})
        self.start()
        self.assertEqual(self.decision(self.write(user)), "ask")
        self.assertEqual(self.decision(self.write(self.root / ".claude" / "patton-gate.json")), "ask")
        self.assertEqual(self.decision(self.bash(f"echo '{{}}' > {user}")), "ask")
        self.assertEqual(self.decision(self.bash("echo x > .claude/patton-gate.json", "p")), "ask")

    def test_a_change_to_the_gates_state_always_asks(self):
        self.start()
        target = self.state_dir() / "granted.json"
        self.assertEqual(self.decision(self.write(target)), "ask")
        self.assertEqual(self.decision(self.bash(f"rm -rf {self.state_dir()}")), "ask")

    def test_under_bypass_permissions_a_change_to_the_gates_files_is_refused(self):
        user = self.user_config({})
        self.start()
        self.assertEqual(self.decision(self.write(user, mode="bypassPermissions")), "deny")

    def test_protect_off_still_guards_the_gates_own_files(self):
        user = self.user_config({"files": {"protect": False}})
        self.start()
        self.assertEqual(self.decision(self.write(user)), "ask")


class AncestorRemovalTests(ConfigCase):
    def test_removing_an_ancestor_of_the_project_names_the_project_and_finishes_fast(self):
        self.start()
        began = time.monotonic()
        result = self.bash("rm -rf /")
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(self.decision(result), "ask")
        self.assertIn("notes.txt", self.reason(result))
        result = self.bash(f"rm -rf {self.root.parent}", "anc")
        self.assertIn("src/app.py", self.reason(result))

    def test_removing_a_directory_outside_the_project_names_nothing_and_walks_nothing(self):
        outside = Path(self.tmp.name) / "elsewhere"
        (outside / "deep").mkdir(parents=True)
        (outside / "deep" / "f").write_text("x")
        self.start()
        walked = []
        real_walk = os.walk
        try:
            gate.os.walk = lambda top, *a, **k: walked.append(top) or real_walk(top, *a, **k)
            self.assertIsNone(self.decision(self.bash(f"rm -rf {outside}")))
        finally:
            gate.os.walk = real_walk
        self.assertEqual(walked, [])


if __name__ == "__main__":
    unittest.main()
