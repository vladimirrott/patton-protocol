"""The patton-gate plugin on Codex CLI: patches, the workdir rewrite, held calls and approval by `approve <id>`.

Codex 0.159 hooks have no `ask`, take `allow` only with an `updatedInput`, never tell the hook a command's `workdir`,
and let a call run when the hook fails. The event shapes follow what Codex sends (see the Codex port design).
"""

from io import StringIO
from pathlib import Path
import json
import unittest
from unittest import mock

from test_gate_config import ConfigCase
from test_gate_plugin import gate


class CodexCase(ConfigCase):
    def setUp(self):
        super().setUp()
        self.env.pop("CLAUDE_PROJECT_DIR")
        self.env["PLUGIN_DATA"] = str(self.home / "codex" / "plugins" / "data" / "patton-gate-patton-protocol")
        self.env["CODEX_HOME"] = str(self.home / "codex")

    def codex(self, event):
        out = StringIO()
        payload = {"session_id": self.session, "cwd": str(self.root), "turn_id": "turn-1", "model": "gpt-6.1-sol",
                   "permission_mode": "bypassPermissions", **event}
        rc = gate.codex_main(StringIO(json.dumps(payload)), out, {**self.env})
        self.assertEqual(rc, 0)
        text = out.getvalue()
        return json.loads(text) if text.strip() else None

    def start(self, source="startup"):
        return self.codex({"hook_event_name": "SessionStart", "source": source})

    def pre(self, tool, command, tool_use_id="c1"):
        return self.codex({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_use_id": tool_use_id,
                           "tool_input": {"command": command}})

    def prompt(self, text):
        return self.codex({"hook_event_name": "UserPromptSubmit", "prompt": text})

    def verdict(self, answer):
        return None if answer is None else answer["hookSpecificOutput"]["permissionDecision"]

    def why(self, answer):
        return answer["hookSpecificOutput"]["permissionDecisionReason"]

    def held_id(self, answer):
        import re
        found = re.search(r"approve ([0-9a-f]{6})", self.why(answer))
        self.assertIsNotNone(found, self.why(answer))
        return found.group(1)

    def state_dir(self):
        return Path(self.env["PLUGIN_DATA"]) / "sessions" / self.session


def patch(*ops):
    return "*** Begin Patch\n" + "\n".join(ops) + "\n*** End Patch\n"


class CodexShellTests(CodexCase):
    def test_an_allowed_command_is_pinned_to_the_directory_the_gate_checked(self):
        self.start()
        answer = self.pre("Bash", "ls -A")
        self.assertEqual(self.verdict(answer), "allow")
        self.assertEqual(answer["hookSpecificOutput"]["updatedInput"]["command"], f"cd {self.root} && ls -A")

    def test_a_protected_change_is_held_with_an_id_and_the_reason_says_how_to_approve(self):
        self.start()
        answer = self.pre("Bash", "rm notes.txt")
        self.assertEqual(self.verdict(answer), "deny")
        self.assertIn("notes.txt", self.why(answer))
        self.held_id(answer)

    def test_approve_in_the_chat_lets_the_same_command_run(self):
        self.start()
        held = self.held_id(self.pre("Bash", "rm notes.txt"))
        context = self.prompt(f"ok, approve {held}")
        self.assertIn("notes.txt", context["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(context["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        self.assertEqual(self.verdict(self.pre("Bash", "rm notes.txt", "c2")), "allow")
        self.assertIn(("UserPromptSubmit", "approved"), [(e["event"], e["verdict"]) for e in self.ledger()])

    def test_an_approval_is_used_once_and_an_unknown_id_says_so(self):
        self.start()
        held = self.held_id(self.pre("Bash", "rm notes.txt"))
        self.prompt(f"approve {held}")
        again = self.prompt(f"approve {held}")
        self.assertIn("no held call", again["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.prompt("please continue"))

    def test_a_command_the_gate_cannot_read_is_held_and_approval_covers_that_command(self):
        self.start()
        held = self.held_id(self.pre("Bash", "python3 -c 'print(1)'"))
        self.prompt(f"approve {held}")
        self.assertEqual(self.verdict(self.pre("Bash", "python3 -c 'print(1)'", "c2")), "allow")
        self.assertEqual(self.verdict(self.pre("Bash", "python3 -c 'print(2)'", "c3")), "deny")

    def test_a_change_to_the_gates_own_state_is_held_too(self):
        self.start()
        answer = self.pre("Bash", f"echo '{{}}' > {self.state_dir() / 'holds.json'}")
        self.assertEqual(self.verdict(answer), "deny")
        self.held_id(answer)

    def test_the_codex_project_file_is_the_one_read(self):
        path = self.root / ".codex" / "patton-gate.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"files": {"on_change": "deny"}}))
        self.start()
        answer = self.pre("Bash", "rm notes.txt")
        self.assertIn("files.on_change", self.why(answer))
        self.assertNotIn("approve", self.why(answer))

    def test_a_command_without_an_absolute_working_directory_is_refused(self):
        self.start()
        answer = self.codex({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "c1",
                             "cwd": "relative", "tool_input": {"command": "ls"}})
        self.assertEqual(self.verdict(answer), "deny")
        self.assertIn("no absolute working directory", self.why(answer))


class CodexLogTests(CodexCase):
    def test_log_and_restore_find_a_codex_session_from_a_shell(self):
        self.start()
        self.pre("Bash", "rm notes.txt")
        shell = {k: v for k, v in self.env.items() if k != "PLUGIN_DATA"}  # a shell has no hook environment
        Path(self.env["PLUGIN_DATA"]).rename(self.home / "codex" / "plugins" / "data" / "patton-gate-x")
        out = StringIO()
        self.assertEqual(gate.log(out, shell, codex=True), 0)
        self.assertIn("rm notes.txt", out.getvalue())
        out = StringIO()
        self.assertEqual(gate.restore_command(["--codex"], out, shell), 0)
        self.assertIn("notes.txt", out.getvalue())


class CodexPatchTests(CodexCase):
    def test_a_patch_that_adds_a_new_file_runs_as_written(self):
        self.start()
        self.assertIsNone(self.pre("apply_patch", patch("*** Add File: NEW.md", "+hi")))

    def test_a_patch_to_a_protected_file_is_held_and_approval_lets_it_run(self):
        self.start()
        body = patch("*** Update File: notes.txt", "@@", "-keep me", "+changed")
        held = self.held_id(self.pre("apply_patch", body))
        self.prompt(f"approve {held}")
        self.assertIsNone(self.pre("apply_patch", body, "c2"))

    def test_a_heredoc_patch_in_the_shell_is_read_as_a_patch(self):
        self.start()
        command = "cd src && apply_patch <<'EOF'\n" + patch("*** Delete File: app.py") + "EOF"
        answer = self.pre("Bash", command)
        self.assertEqual(self.verdict(answer), "deny")
        self.assertIn("src/app.py", self.why(answer))

    def test_a_patch_the_gate_cannot_read_is_refused(self):
        self.start()
        answer = self.pre("apply_patch", "not a patch")
        self.assertEqual(self.verdict(answer), "deny")
        self.assertIn("this patch cannot be checked", self.why(answer))

    def test_a_move_names_both_ends(self):
        self.start()
        answer = self.pre("apply_patch", patch("*** Update File: notes.txt", "*** Move to: moved.txt", "@@", "-keep me",
                                               "+keep me"))
        self.assertIn("notes.txt", self.why(answer))


class CodexFailClosedTests(CodexCase):
    def test_a_crash_refuses_the_call(self):
        self.start()
        with mock.patch.object(gate, "_run", side_effect=RuntimeError("boom")):
            answer = self.pre("Bash", "ls")
        self.assertEqual(self.verdict(answer), "deny")
        self.assertIn("boom", self.why(answer))

    def test_a_crash_on_a_prompt_never_blocks_the_person(self):
        self.start()
        with mock.patch.object(gate, "_codex_approve", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.prompt("approve abcdef"))

    def test_an_unknown_tool_is_refused(self):
        self.start()
        answer = self.pre("mcp__x__y", "anything")
        self.assertEqual(self.verdict(answer), "deny")
        self.assertIn("not one the gate can check", self.why(answer))

    def test_unreadable_input_is_refused(self):
        out = StringIO()
        self.assertEqual(gate.codex_main(StringIO("{not json"), out, {**self.env}), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_broken_configuration_refuses_every_call(self):
        self.user_config("{broken")
        self.start()
        self.assertEqual(self.verdict(self.pre("Bash", "ls")), "deny")


class CodexPackagingTests(unittest.TestCase):
    PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "patton-gate"

    def test_the_codex_manifest_points_at_its_own_hooks_and_matches_the_version(self):
        manifest = json.loads((self.PLUGIN / ".codex-plugin" / "plugin.json").read_text())
        claude = json.loads((self.PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual((manifest["name"], manifest["version"]), (claude["name"], claude["version"]))
        self.assertEqual(manifest["version"], gate.VERSION)
        hooks = json.loads((self.PLUGIN / manifest["hooks"]).read_text())["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "PreToolUse", "UserPromptSubmit"})
        self.assertEqual(hooks["PreToolUse"][0]["matcher"], "^(Bash|apply_patch)$")
        pre = hooks["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn("--host codex", pre)
        self.assertIn("exit 2", pre)  # no python3: Codex blocks on exit 2
        for event in ("SessionStart", "UserPromptSubmit"):  # never block the person over a missing python3
            self.assertIn("|| exit 0;", hooks[event][0]["hooks"][0]["command"])

    def test_the_codex_marketplace_lists_the_same_plugin_directory(self):
        market = json.loads((self.PLUGIN.parents[1] / ".agents" / "plugins" / "marketplace.json").read_text())
        self.assertEqual(market["name"], "patton-protocol")
        entry = market["plugins"][0]
        self.assertEqual((entry["name"], entry["source"]["path"]), ("patton-gate", "./plugins/patton-gate"))


if __name__ == "__main__":
    unittest.main()
