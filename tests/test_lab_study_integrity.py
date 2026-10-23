"""The committed study, its report, and the documents that quote it must agree.

These tests read what the repository ships: lab/results, lab/cassettes, lab/REPORT.md,
the pre-registration, and the prose that cites them. Each check is a function over data
so a second test can feed it a bad input and prove it fails.
"""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import copy
import re
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from patton_lab import prereg, report, run  # noqa: E402
from patton_lab.experiments import SPEND_ORDER  # noqa: E402

DECIMAL = re.compile(r"-?\d+\.\d+")
PAIR = re.compile(r"\b(\d+) of (\d+)\b")
COUNT = re.compile(r"\b(\d+) (?:study |smoke )?(?:jobs|runs)\b")
INTEGER = re.compile(r"(?<![\d.])\d+(?!\d|\.\d)")  # "240." ending a sentence is the integer 240
EXPERIMENT = re.compile(r"\bE\d[A-Z]*\b")
KNOWN_FAMILIES = {"E5", "E6", "E7"}  # E5 and E6 were cut in the design; E7 names the family of E7S
PROSE = ("README.md", "lab/README.md", "docs/plans/2026-10-12-patton-lab-design.md",
         "docs/plans/2026-10-12-patton-lab-implementation.md", "docs/plans/2026-10-19-real-harness-gate-design.md")


def frozen_hashes(prereg_text):
    return set(re.findall(r"`([0-9a-f]{64})`", prereg_text)) - set(
        re.findall(r"Fixture sources sha256: `([0-9a-f]{64})`", prereg_text))


def planned_jobs(prereg_text):
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"^\| (E\w+) \| [^|]+ \| \d+ \| \d+ \| (\d+) \| [^|]+ \|$", prereg_text, re.M)}


def stray_hashes(data, prereg_text):
    allowed = frozen_hashes(prereg_text)
    return sorted({f"{exp}:{r['arm']}" for exp, rows in data.items() for r in rows if r.get("arm_hash") not in allowed})


def count_problems(data, prereg_text):
    planned = planned_jobs(prereg_text)
    problems = []
    for exp, rows in data.items():
        if exp == "SMOKE":
            continue
        if exp not in planned:
            problems.append(f"{exp}: results exist for an experiment the pre-registration does not list")
            continue
        jobs = [r["job"] for r in rows]
        if len(set(jobs)) != len(jobs):
            problems.append(f"{exp}: duplicate job ids")
        if len(jobs) != planned[exp]:
            problems.append(f"{exp}: {len(jobs)} recorded, {planned[exp]} registered")
    for exp in planned:
        if exp not in data and exp != "E1H":  # E1H runs only if P4a and P4b are both SUPPORTED
            problems.append(f"{exp}: registered but not recorded")
    return problems


# A version string is a requirement, not a result: `python3` 3.10, Ubuntu 22.04, 24.04 and 26.04.
VERSIONS = re.compile(r"(?:Ubuntu|Python|python3`?)\s+\d+\.\d+(?:(?:,\s*|\s+and\s+|,\s+and\s+)\d+\.\d+)*")


def unbacked_numbers(doc_text, *sources):
    """Decimals and 'N of M' pairs in `doc_text` that no source prints. Version strings are not results."""
    doc_text = VERSIONS.sub("", doc_text)
    source_text = "\n".join(sources)
    printed = {d.lstrip("-") for d in DECIMAL.findall(source_text)}  # the sign is carried in words, so compare magnitudes
    printed_pairs = set(PAIR.findall(source_text))
    printed_integers = set(INTEGER.findall(source_text))
    decimals = [d for d in DECIMAL.findall(doc_text) if d.lstrip("-") not in printed]
    pairs = [f"{a} of {b}" for a, b in PAIR.findall(doc_text) if (a, b) not in printed_pairs]
    counts = [f"{c} runs/jobs" for c in COUNT.findall(doc_text) if c not in printed_integers]
    return decimals + pairs + counts


CASSETTE_CAP_BYTES = 25 * 1024 * 1024  # design section 10: past this the cassettes move to a release asset


def directory_bytes(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def unknown_experiments(text):
    known = set(SPEND_ORDER) | {"SMOKE"} | KNOWN_FAMILIES
    return sorted(set(EXPERIMENT.findall(text)) - known)


class CommittedStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prereg_text = prereg.PREREG_PATH.read_text(encoding="utf-8")
        cls.data = report.load(LAB_ROOT)
        cls.report_text = (LAB_ROOT / "REPORT.md").read_text(encoding="utf-8")

    def test_the_study_is_there(self):
        self.assertGreaterEqual(sum(len(rows) for rows in self.data.values()), 1000,
                                "an empty results directory would make every check below pass")

    def test_committed_report_is_what_the_results_produce(self):
        text, _ = report.build(LAB_ROOT)
        self.assertEqual(self.report_text, text, "regenerate with: python3 -m patton_lab.run report")

    def test_every_recorded_row_ran_a_frozen_prompt(self):
        self.assertEqual(stray_hashes(self.data, self.prereg_text), [])

    def test_recorded_counts_equal_the_registered_counts(self):
        self.assertEqual(count_problems(self.data, self.prereg_text), [])

    def test_the_committed_cassettes_replay_every_job_exactly(self):
        out = StringIO()
        with redirect_stdout(out):
            code = run.main(["replay"], root=LAB_ROOT)
        self.assertEqual(code, 0, out.getvalue()[-600:])
        self.assertRegex(out.getvalue(), r"replay OK: \d{4} job\(s\) reproduce exactly")

    def test_findings_quote_only_numbers_the_report_or_the_preregistration_print(self):
        # The gate study's findings quote lab/CC_REPORT.md; tests/test_cc_study_integrity.py checks them.
        docs = sorted(p for p in (REPO_ROOT / "docs" / "findings").glob("*.md") if "gate-study" not in p.name)
        self.assertTrue(docs)
        for doc in docs:
            text = doc.read_text(encoding="utf-8")
            self.assertGreater(len(DECIMAL.findall(text)) + len(PAIR.findall(text)), 10,
                               f"{doc.name} must quote numbers for this check to mean anything")
            self.assertEqual(unbacked_numbers(text, self.report_text, self.prereg_text), [], doc.name)

    def test_the_readmes_quote_only_backed_numbers(self):
        # The READMEs quote every study, so a number may come from either report or any pre-registration.
        gate_report = (LAB_ROOT / "CC_REPORT.md").read_text(encoding="utf-8")
        preregs = [self.prereg_text] + [(LAB_ROOT / "prereg" / name).read_text(encoding="utf-8") for name in
                                        ("2026-10-21-gate-study-preregistration.md",
                                         "2026-10-22-codex-gate-study-preregistration.md")]
        report_text = self.report_text + "\n" + gate_report
        for rel in ("README.md", "lab/README.md"):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertEqual(unbacked_numbers(text, report_text, "\n".join(preregs)), [], rel)

    def test_cassettes_stay_under_the_size_cap(self):
        size = directory_bytes(LAB_ROOT / "cassettes")
        self.assertGreater(size, 1_000_000, "an empty cassette directory would pass the cap trivially")
        self.assertLessEqual(size, CASSETTE_CAP_BYTES)

    def test_prose_names_only_experiments_that_exist(self):
        for rel in PROSE:
            self.assertEqual(unknown_experiments((REPO_ROOT / rel).read_text(encoding="utf-8")), [], rel)


class TheChecksCanFailTests(unittest.TestCase):
    """Each guard above is vacuous if it cannot fail, so feed each one a bad input."""

    @classmethod
    def setUpClass(cls):
        cls.prereg_text = prereg.PREREG_PATH.read_text(encoding="utf-8")
        cls.data = report.load(LAB_ROOT)

    def test_a_row_with_an_unfrozen_hash_is_named(self):
        bad = copy.deepcopy(self.data)
        bad["E1"][0]["arm_hash"] = "0" * 64
        self.assertEqual(stray_hashes(bad, self.prereg_text), [f"E1:{bad['E1'][0]['arm']}"])

    def test_a_dropped_or_repeated_job_is_named(self):
        dropped = copy.deepcopy(self.data)
        dropped["E2"].pop()
        self.assertEqual(count_problems(dropped, self.prereg_text), ["E2: 383 recorded, 384 registered"])
        repeated = copy.deepcopy(self.data)
        repeated["E3"][1] = copy.deepcopy(repeated["E3"][0])
        self.assertIn("E3: duplicate job ids", count_problems(repeated, self.prereg_text))
        missing = {k: v for k, v in self.data.items() if k != "E4"}
        self.assertIn("E4: registered but not recorded", count_problems(missing, self.prereg_text))

    def test_the_registered_counts_are_actually_parsed(self):
        planned = planned_jobs(self.prereg_text)
        self.assertEqual(planned["E1"], 560)
        self.assertEqual(len(planned), len(SPEND_ORDER))

    def test_a_typed_number_and_a_wrong_count_are_named(self):
        report_text = (LAB_ROOT / "REPORT.md").read_text(encoding="utf-8")
        doc = "Harm was 0.4242 in 63 of 64 runs, and 0.325 in 80 runs, over 1347 study jobs."
        self.assertEqual(unbacked_numbers(doc, report_text, self.prereg_text),
                         ["0.4242", "63 of 64", "1347 runs/jobs"])

    def test_a_version_after_ubuntu_or_python_is_not_a_result_and_a_result_beside_it_still_is(self):
        report_text = (LAB_ROOT / "REPORT.md").read_text(encoding="utf-8")
        doc = "Needs `python3` 3.10 or newer, checked in Ubuntu 22.04, 24.04 and 26.04 VMs; harm was 0.4242."
        self.assertEqual(unbacked_numbers(doc, report_text, self.prereg_text), ["0.4242"])

    def test_the_size_measure_counts_every_file(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "sub").mkdir()
            (Path(tmp) / "a.gz").write_bytes(b"x" * 7)
            (Path(tmp) / "sub" / "b.gz").write_bytes(b"y" * 5)
            self.assertEqual(directory_bytes(tmp), 12)

    def test_a_retired_experiment_name_is_named(self):
        self.assertEqual(unknown_experiments("E1 and E7G and E1R and E9"), ["E7G", "E9"])


if __name__ == "__main__":
    unittest.main()
