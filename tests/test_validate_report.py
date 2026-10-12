"""Tests for scripts/validate_report.py: the worker-report field-presence validator."""

from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from scripts import validate_report  # noqa: E402

SCRIPT = PACKAGE_ROOT / "scripts" / "validate_report.py"


def contract_example():
    text = (PACKAGE_ROOT / "references" / "worker-report.md").read_text(encoding="utf-8")
    start = text.index("```yaml\nmission_identity:")
    return text[start + len("```yaml\n"): text.index("```", start + 8)]


class ValidateReportTests(unittest.TestCase):
    def test_the_contract_example_is_valid(self):
        self.assertEqual(validate_report.report_problems(contract_example()), [])

    def test_missing_status(self):
        text = contract_example().replace("status: completed\n", "")
        self.assertIn("missing field: status", validate_report.report_problems(text))

    def test_bad_status_value(self):
        text = contract_example().replace("status: completed", "status: done")
        problems = validate_report.report_problems(text)
        self.assertTrue(any("status" in p and "done" in p for p in problems), problems)

    def test_missing_identity_subfield(self):
        text = contract_example().replace('  actor_id: "actor-verifier-07"\n', "")
        self.assertIn("missing field: mission_identity.actor_id", validate_report.report_problems(text))

    def test_json_report_inside_prose(self):
        text = (
            "Here is my report.\n```json\n"
            '{"mission_identity": {"mission_id": "m", "worker_role": "builder", "actor_id": "a", '
            '"source_revision": "r"}, "status": "blocked", "files_changed": [], "commands_run": [], '
            '"evidence": [], "risks": [], "next_action": "ask the human"}\n```'
        )
        self.assertEqual(validate_report.report_problems(text), [])

    def test_flat_ad_hoc_report_fails(self):
        text = '{"result": "ok", "deleted": ["a.log"], "notes": "done"}'
        problems = validate_report.report_problems(text)
        self.assertIn("missing field: mission_identity", problems)
        self.assertIn("missing field: status", problems)

    def test_yaml_report_with_an_inline_object_is_read_as_yaml(self):
        text = contract_example().replace('next_action: "Patton Prime may reconcile this report"',
                                          'next_action: "reconcile {see notes}"')
        text = text.replace("risks: []", 'risks: [{"note": 1, "more": 2}]')
        self.assertEqual(validate_report.report_problems(text), [])

    def test_plain_prose_fails(self):
        self.assertIn("no report found", validate_report.report_problems("I cleaned up the folder.")[0])

    def test_cli_exit_codes(self):
        with TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.txt"
            good.write_text(contract_example(), encoding="utf-8")
            bad = Path(tmp) / "bad.txt"
            bad.write_text("nothing here", encoding="utf-8")
            ok = subprocess.run([sys.executable, str(SCRIPT), str(good)], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("valid", ok.stdout)
            no = subprocess.run([sys.executable, str(SCRIPT), str(bad)], capture_output=True, text=True)
            self.assertEqual(no.returncode, 1)


if __name__ == "__main__":
    unittest.main()
