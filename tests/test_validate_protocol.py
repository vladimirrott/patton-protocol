"""Contract tests for the canonical Patton Protocol package metadata."""

from pathlib import Path
import re
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PACKAGE_ROOT / "SKILL.md"
OPENAI_METADATA_PATH = PACKAGE_ROOT / "agents" / "openai.yaml"
MISSION_CONTRACT_PATH = PACKAGE_ROOT / "references" / "mission-contract.md"
WORKER_REPORT_PATH = PACKAGE_ROOT / "references" / "worker-report.md"
LIFECYCLE_TERMS = (
    "scope",
    "plan",
    "dispatch",
    "observe",
    "reconcile",
    "verify",
    "report",
)

MISSION_FIELDS = (
    "mission identity",
    "objective",
    "inputs",
    "allowed paths",
    "stopping condition",
    "budget",
    "timeout",
    "retry limit",
    "evidence",
)

REPORT_FIELDS = (
    "mission identity",
    "status",
    "files changed",
    "commands run",
    "evidence",
    "risks",
    "next action",
)

MISSION_ENVELOPE_KEYS = {
    "mission_id",
    "worker_role",
    "source_revision",
    "objective",
    "inputs",
    "allowed_paths",
    "stopping_condition",
    "budget",
    "timeout",
    "retry_limit",
    "evidence",
}
REPORT_ENVELOPE_KEYS = {
    "mission_identity",
    "status",
    "files_changed",
    "commands_run",
    "evidence",
    "risks",
    "next_action",
}
IDENTITY_KEYS = {"mission_id", "worker_role", "source_revision"}
ALLOWED_STATUSES = {"completed", "partial", "blocked", "failed"}


def read_frontmatter(path: Path) -> dict[str, str]:
    """Read the simple scalar YAML frontmatter used by an Agent Skill."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}

    frontmatter: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"\'')
    return frontmatter


def read_text_or_empty(path: Path) -> str:
    """Return an empty document while a package artifact is absent."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def parse_first_yaml_block(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Parse top-level keys and one nested mapping from a YAML-shaped example."""
    content = read_text_or_empty(path)
    blocks = re.findall(r"```ya?ml\s*\n(.*?)```", content, re.DOTALL)
    if not blocks:
        return {}, {}

    top_level: dict[str, str] = {}
    nested: dict[str, str] = {}
    parent: str | None = None
    for line in blocks[0].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^(\s*)([A-Za-z_][\w-]*):(?:\s*(.*))?$", line)
        if not match:
            continue
        indent, key, value = match.groups()
        value = (value or "").strip().strip('"\'')
        if not indent:
            top_level[key] = value
            parent = key if not value else None
        elif parent == "mission_identity":
            nested[key] = value
    return top_level, nested


class ProtocolMetadataTests(unittest.TestCase):
    def test_skill_has_canonical_frontmatter(self) -> None:
        metadata = read_frontmatter(SKILL_PATH)

        self.assertEqual(metadata.get("name"), "patton-protocol")
        self.assertTrue(metadata.get("description"))

    def test_description_states_delegation_trigger(self) -> None:
        description = read_frontmatter(SKILL_PATH).get("description", "").lower()

        for term in ("bounded", "independent", "delegation", "improves"):
            with self.subTest(term=term):
                self.assertIn(term, description)

    def test_skill_declares_portable_lifecycle(self) -> None:
        content = read_text_or_empty(SKILL_PATH).lower()

        for term in LIFECYCLE_TERMS:
            with self.subTest(term=term):
                self.assertIn(term, content)

    def test_skill_uses_neutral_lifecycle_placeholders(self) -> None:
        content = read_text_or_empty(SKILL_PATH)

        for term in LIFECYCLE_TERMS:
            heading = rf"^### {term.title()}\s*$"
            with self.subTest(term=term):
                self.assertRegex(content, re.compile(heading, re.MULTILINE))


class ProtocolContractTests(unittest.TestCase):
    def test_mission_contract_defines_required_fields(self) -> None:
        content = read_text_or_empty(MISSION_CONTRACT_PATH).lower()

        for field in MISSION_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, content)

    def test_mission_example_has_exact_required_keys(self) -> None:
        top_level, _ = parse_first_yaml_block(MISSION_CONTRACT_PATH)

        self.assertEqual(set(top_level), MISSION_ENVELOPE_KEYS)

    def test_report_example_has_exact_required_keys(self) -> None:
        top_level, nested = parse_first_yaml_block(WORKER_REPORT_PATH)

        self.assertEqual(set(top_level), REPORT_ENVELOPE_KEYS)
        self.assertEqual(set(nested), IDENTITY_KEYS)

    def test_report_identity_matches_mission_identity(self) -> None:
        mission, _ = parse_first_yaml_block(MISSION_CONTRACT_PATH)
        report, report_identity = parse_first_yaml_block(WORKER_REPORT_PATH)

        self.assertEqual(set(mission) & IDENTITY_KEYS, IDENTITY_KEYS)
        self.assertEqual(set(report_identity), IDENTITY_KEYS)
        for key in IDENTITY_KEYS:
            with self.subTest(key=key):
                self.assertEqual(mission[key], report_identity[key])
        self.assertEqual(report["mission_identity"], "")

    def test_worker_report_defines_required_fields(self) -> None:
        content = read_text_or_empty(WORKER_REPORT_PATH).lower()

        for field in REPORT_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, content)

    def test_worker_report_documents_yaml_envelope_and_allowed_statuses(self) -> None:
        content = read_text_or_empty(WORKER_REPORT_PATH).lower()
        top_level, _ = parse_first_yaml_block(WORKER_REPORT_PATH)

        self.assertIn("mission_identity", top_level)
        self.assertIn(top_level["status"], ALLOWED_STATUSES)
        documented_statuses = set(re.findall(r"^- `([a-z_]+)`: ", content, re.MULTILINE))
        self.assertEqual(documented_statuses, ALLOWED_STATUSES)

    def test_timeout_classifies_reports_by_usable_evidence(self) -> None:
        content = read_text_or_empty(WORKER_REPORT_PATH).lower()
        content = " ".join(content.split())

        self.assertIn(
            "patton prime maps a timeout with usable evidence to `partial`; a timeout with no usable evidence maps to `failed`",
            content,
        )

    def test_worker_report_separates_evidence_leads_from_approval(self) -> None:
        content = read_text_or_empty(WORKER_REPORT_PATH).lower()

        self.assertIn("evidence", content)
        self.assertIn("lead", content)
        self.assertRegex(content, re.compile(r"approval decision|approve|approval gate"))

    def test_skill_links_mission_and_worker_report_contracts(self) -> None:
        content = read_text_or_empty(SKILL_PATH)

        for reference in ("references/mission-contract.md", "references/worker-report.md"):
            with self.subTest(reference=reference):
                self.assertIn(f"]({reference})", content)
                self.assertTrue((PACKAGE_ROOT / reference).is_file())

    def test_skill_documents_serial_fallback(self) -> None:
        content = read_text_or_empty(SKILL_PATH).lower()

        self.assertIn("serial", content)
        self.assertRegex(content, re.compile(r"cannot spawn|unable to spawn|no worker spawn"))

    def test_codex_discovery_metadata_names_skill(self) -> None:
        content = read_text_or_empty(OPENAI_METADATA_PATH)

        self.assertIn("display_name:", content)
        self.assertIn("short_description:", content)
        self.assertIn("default_prompt:", content)
        self.assertIn("patton-protocol", content)


if __name__ == "__main__":
    unittest.main()
