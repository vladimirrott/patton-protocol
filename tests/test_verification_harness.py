"""Tests for verification/verify_cassettes.py and the verification/ privacy gate.

verify_cassettes.py is a guard. A guard that answers "OK" when it could not
look is worse than none, so most cases here break something and require a
non-zero exit, including the cases where the evidence has been removed rather
than altered.
"""

from contextlib import redirect_stdout
from pathlib import Path
import hashlib
import importlib.util
import io
import re
from tempfile import TemporaryDirectory
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
VERIFICATION_ROOT = PACKAGE_ROOT / "verification"

_spec = importlib.util.spec_from_file_location(
    "verify_cassettes", VERIFICATION_ROOT / "verify_cassettes.py"
)
verify_cassettes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_cassettes)

# Assembled at runtime so this file does not contain the string it forbids.
EMPLOYER_TOKEN = "entr" + "opia"
HOME_PATH = re.compile(r"/home/([A-Za-z0-9_.-]+)")


def build_root(root, trials=1, with_log=True):
    """Write a minimal verification root: cassettes, manifests, and a log."""
    log_lines = ["# Verification log", ""]
    for index in range(trials):
        trial = root / "cassettes" / "h" / f"m{index}" / "2026-01-01T000000Z"
        trial.mkdir(parents=True)
        (trial / "run_metadata.md").write_text(f"trial {index}\n", encoding="utf-8")
        (trial / "worker_report.txt").write_text(f"report {index}\n", encoding="utf-8")
        verify_cassettes.write_manifest(trial)
        digest = hashlib.sha256((trial / "manifest.sha256").read_bytes()).hexdigest()
        log_lines.append(f"- trial {index} manifest digest: `{digest}`")
    if with_log:
        (root / "log.md").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


def only_trial(root):
    return sorted((root / "cassettes").rglob("run_metadata.md"))[0].parent


def run_main(root):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        rc = verify_cassettes.main([], root=root)
    return rc, buffer.getvalue()


class CassetteCheckTests(unittest.TestCase):
    def test_clean_root_passes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root, trials=2)
            rc, out = run_main(root)
            self.assertEqual(rc, 0, out)
            self.assertIn("2 cassette", out)

    def test_altered_artifact_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root)
            with (only_trial(root) / "worker_report.txt").open("a", encoding="utf-8") as f:
                f.write("x")
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertIn("hash mismatch", out)

    def test_deleted_manifest_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root, trials=2)
            (only_trial(root) / "manifest.sha256").unlink()
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertIn("missing manifest.sha256", out)

    def test_every_manifest_deleted_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root, trials=2)
            for manifest in (root / "cassettes").rglob("manifest.sha256"):
                manifest.unlink()
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertNotIn("OK", out)

    def test_no_cassettes_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cassettes").mkdir()
            (root / "log.md").write_text("# log\n", encoding="utf-8")
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertIn("no cassettes", out)

    def test_missing_cassettes_dir_fails(self):
        with TemporaryDirectory() as tmp:
            rc, out = run_main(Path(tmp))
            self.assertEqual(rc, 1, out)

    def test_digest_absent_from_log_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root, trials=2)
            log = (root / "log.md").read_text(encoding="utf-8").splitlines()
            (root / "log.md").write_text("\n".join(log[:-1]) + "\n", encoding="utf-8")
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertIn("not recorded in log.md", out)

    def test_rehashed_manifest_is_caught_by_the_log(self):
        # An editor who alters an artifact and regenerates its manifest passes
        # the per-cassette check. The log digest is what exposes the edit.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root)
            trial = only_trial(root)
            with (trial / "worker_report.txt").open("a", encoding="utf-8") as f:
                f.write("forged\n")
            verify_cassettes.write_manifest(trial)
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertIn("not recorded in log.md", out)

    def test_missing_log_fails(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_root(root, with_log=False)
            rc, out = run_main(root)
            self.assertEqual(rc, 1, out)
            self.assertIn("log.md is missing", out)


class CommittedVerificationTests(unittest.TestCase):
    def test_committed_cassettes_verify(self):
        problems = verify_cassettes.check(VERIFICATION_ROOT)
        self.assertEqual(problems, [])

    def test_committed_cassette_count_is_the_known_nine(self):
        # A silent drop in this number is the failure the guard exists to stop.
        self.assertEqual(len(verify_cassettes.find_trial_dirs(VERIFICATION_ROOT)), 9)


class PrivacyGateTests(unittest.TestCase):
    """Recorded transcripts must not carry the author's machine identity."""

    def text_files(self):
        return [
            path
            for path in sorted(VERIFICATION_ROOT.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        ]

    def test_scan_covers_files(self):
        self.assertGreater(len(self.text_files()), 60)

    def test_no_employer_token_anywhere(self):
        offenders = [
            str(path.relative_to(PACKAGE_ROOT))
            for path in self.text_files()
            if EMPLOYER_TOKEN in path.read_text(encoding="utf-8", errors="replace").lower()
        ]
        self.assertEqual(offenders, [])

    def test_no_real_home_directory(self):
        offenders = []
        for path in self.text_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in HOME_PATH.finditer(text):
                if match.group(1) != "user":
                    offenders.append(f"{path.relative_to(PACKAGE_ROOT)}: {match.group(0)}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
