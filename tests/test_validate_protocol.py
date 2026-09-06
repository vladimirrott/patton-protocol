"""Contract tests for the canonical Patton Protocol package metadata."""

from pathlib import Path
import hashlib
import re
import stat
from tempfile import TemporaryDirectory
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PACKAGE_ROOT / "SKILL.md"
OPENAI_METADATA_PATH = PACKAGE_ROOT / "agents" / "openai.yaml"
MISSION_CONTRACT_PATH = PACKAGE_ROOT / "references" / "mission-contract.md"
WORKER_REPORT_PATH = PACKAGE_ROOT / "references" / "worker-report.md"
SAFETY_BUDGETS_PATH = PACKAGE_ROOT / "references" / "safety-and-budgets.md"
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
    "candidate revision",
    "content digest",
)

REPORT_FIELDS = (
    "mission identity",
    "status",
    "files changed",
    "commands run",
    "evidence",
    "risks",
    "next action",
    "candidate revision",
    "content digest",
)

MISSION_ENVELOPE_KEYS = {
    "mission_id",
    "worker_role",
    "actor_id",
    "source_revision",
    "objective",
    "inputs",
    "allowed_paths",
    "stopping_condition",
    "budget",
    "timeout",
    "retry_limit",
    "evidence",
    "candidate_revision",
    "content_digest",
}
REPORT_ENVELOPE_KEYS = {
    "mission_identity",
    "status",
    "files_changed",
    "commands_run",
    "evidence",
    "risks",
    "next_action",
    "candidate_revision",
    "content_digest",
}
IDENTITY_KEYS = {"mission_id", "worker_role", "source_revision", "actor_id"}
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


def canonical_fixture_digest(root: Path) -> str:
    """Compute the deterministic digest described by the candidate contract."""
    records: list[bytes] = []
    paths = (
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file()
        and not candidate.is_symlink()
        and ".git" not in candidate.relative_to(root).parts
        and candidate.relative_to(root).parts[:2] != (".patton", "ledger")
    )
    for path in sorted(paths, key=lambda candidate: candidate.relative_to(root).as_posix().encode("utf-8")):
        relative_path = path.relative_to(root).as_posix().encode("utf-8")
        mode = f"{stat.S_IMODE(path.stat().st_mode):04o}".encode("ascii")
        payload = path.read_bytes()
        records.append(
            relative_path
            + b"\0"
            + mode
            + b"\0"
            + str(len(payload)).encode("ascii")
            + b"\0"
            + payload
        )
    return hashlib.sha256(b"".join(record + b"\n" for record in records)).hexdigest()


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

    def test_candidate_identity_is_required_after_builder_mutation(self) -> None:
        mission = read_text_or_empty(MISSION_CONTRACT_PATH).lower()
        report = read_text_or_empty(WORKER_REPORT_PATH).lower()
        skill = read_text_or_empty(SKILL_PATH).lower()

        for field in ("candidate_revision", "content_digest"):
            with self.subTest(field=field):
                self.assertIn(field, mission)
                self.assertIn(field, report)
                self.assertIn(field, skill)
        self.assertRegex(
            " ".join((mission + report + skill).split()),
            re.compile(r"post-build mutation.{0,160}(stale|reject|invalid)", re.IGNORECASE),
        )
        self.assertRegex(
            " ".join((mission + report + skill).split()),
            re.compile(
                r"prime.{0,180}verifier.{0,180}(candidate_revision|content_digest).{0,100}match",
                re.IGNORECASE,
            ),
        )

    def test_post_build_mutation_rejects_stale_candidate_evidence(self) -> None:
        content = " ".join(
            (read_text_or_empty(SKILL_PATH) + read_text_or_empty(SAFETY_BUDGETS_PATH)).lower().split()
        )
        self.assertRegex(
            content,
            re.compile(
                r"(candidate_revision.{0,120}content_digest|content_digest.{0,120}candidate_revision)."
                r"{0,240}(match|same|reject|stale)",
                re.IGNORECASE,
            ),
        )

    def test_fixture_digest_changes_after_post_build_mutation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "candidate.txt"
            source.parent.mkdir()
            source.write_bytes(b"candidate-v1\n")

            locked_digest = canonical_fixture_digest(root)
            source.write_bytes(b"candidate-v2\n")
            current_digest = canonical_fixture_digest(root)

        self.assertNotEqual(locked_digest, current_digest)
        self.assertNotEqual(
            {"content_digest": locked_digest},
            {"content_digest": current_digest},
            "post-build mutation makes prior evidence stale",
        )

        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(MISSION_CONTRACT_PATH)
                + read_text_or_empty(WORKER_REPORT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        for term in ("prime computes", "sha-256", "sorted", "relative path", "byte length"):
            with self.subTest(term=term):
                self.assertIn(term, content)
        self.assertRegex(content, re.compile(r"verifier.{0,200}(recomputes|recompute).{0,200}(before|after)", re.IGNORECASE))

    def test_out_of_allowlist_behavior_file_invalidates_candidate_identity(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "candidate.txt"
            config = root / "config.ini"
            source.parent.mkdir()
            source.write_bytes(b"candidate\n")
            config.write_bytes(b"feature=old\n")
            allowed_paths = {"src/"}

            locked_digest = canonical_fixture_digest(root)
            config.write_bytes(b"feature=new\n")
            current_digest = canonical_fixture_digest(root)

        self.assertIn("src/", allowed_paths)
        self.assertNotIn("config.ini", allowed_paths)
        self.assertNotEqual(locked_digest, current_digest)

        content = " ".join(
            read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split()
        )
        for term in (
            "candidate-relevant repository snapshot",
            "outside the mutation allowlist",
            "behavior-affecting",
            "file mode",
            "permission bits",
        ):
            with self.subTest(term=term):
                self.assertIn(term, content)
        self.assertRegex(
            content,
            re.compile(
                r"outside.{0,180}(allowlist|allowed_paths).{0,180}(digest|reject|blocked)",
                re.IGNORECASE,
            ),
        )

    def test_file_mode_metadata_contributes_to_candidate_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "candidate.txt"
            source.write_bytes(b"same bytes\n")
            initial_mode = source.stat().st_mode
            locked_digest = canonical_fixture_digest(root)
            source.chmod(stat.S_IMODE(initial_mode) ^ stat.S_IXUSR)
            mode_changed_digest = canonical_fixture_digest(root)

        self.assertNotEqual(locked_digest, mode_changed_digest)

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

    def test_serial_fallback_requires_distinct_verifier_actor(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(WORKER_REPORT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )

        for phrase in ("serial fallback", "distinct verifier identity", "same actor", "unverified", "blocked"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, content)
        self.assertRegex(
            content,
            re.compile(r"serial fallback.{0,300}(builder|verifier).{0,300}(same actor|distinct)", re.IGNORECASE),
        )

    def test_denied_approval_blocks_irreversible_action(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(WORKER_REPORT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )

        self.assertRegex(content, re.compile(r"denied.{0,100}(approval|blocked)", re.IGNORECASE))

    def test_missing_approval_blocks_irreversible_action(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )

        self.assertRegex(content, re.compile(r"missing.{0,100}approval.{0,100}blocked", re.IGNORECASE))

    def test_automated_host_only_approval_does_not_satisfy_gate(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(WORKER_REPORT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )

        self.assertIn("automated host-only approval", content)
        self.assertRegex(content, re.compile(r"automated host-only approval.{0,100}(not|does not).{0,100}(satisfy|grant|blocked)", re.IGNORECASE))
        self.assertNotIn("human or host approval", content)

    def test_valid_approval_requires_explicit_human_identity_and_evidence(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )

        self.assertRegex(
            content,
            re.compile(
                r"valid approval.{0,180}human approver identity.{0,180}(scope|evidence)",
                re.IGNORECASE,
            ),
        )

    def test_codex_discovery_metadata_names_skill(self) -> None:
        content = read_text_or_empty(OPENAI_METADATA_PATH)

        self.assertIn("display_name:", content)
        self.assertIn("short_description:", content)
        self.assertIn("default_prompt:", content)
        self.assertIn("patton-protocol", content)


class PortableWorkflowTests(unittest.TestCase):
    """The canonical workflow must remain portable and bounded."""

    def test_skill_states_required_orchestration_safeguards(self) -> None:
        content = " ".join(read_text_or_empty(SKILL_PATH).lower().split())

        required_rules = (
            "delegation threshold",
            "bounded mission",
            "immutable ownership",
            "recheck",
            "conflict",
            "partial failure",
            "human approval",
            "serial fallback",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, content)

    def test_safety_reference_defines_operational_limits(self) -> None:
        content = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())

        self.assertTrue(SAFETY_BUDGETS_PATH.is_file())
        limits = (
            "worker count",
            "mission budget",
            "timeout",
            "retry",
            "mutable-file overlap",
            "approval",
            "disagreement",
        )
        for limit in limits:
            with self.subTest(limit=limit):
                self.assertIn(limit, content)

    def test_skill_defines_patton_roles_and_boundaries(self) -> None:
        content = read_text_or_empty(SKILL_PATH).lower()

        for role in ("patton prime", "scout", "builder", "verifier", "quartermaster"):
            with self.subTest(role=role):
                self.assertIn(role, content)
        self.assertRegex(content, re.compile(r"immutable.{0,80}ownership", re.IGNORECASE | re.DOTALL))

    def test_skill_links_safety_reference(self) -> None:
        content = read_text_or_empty(SKILL_PATH)

        self.assertIn("](references/safety-and-budgets.md)", content)
        self.assertTrue(SAFETY_BUDGETS_PATH.is_file())

    def test_core_does_not_require_vendor_command_syntax(self) -> None:
        content = read_text_or_empty(SKILL_PATH).lower()
        forbidden_required_syntax = (
            r"\bclaude\s+--",
            r"\bcodex\s+(?:exec|run)\b",
            r"\bcursor\s+--",
            r"\bspawn_agent\s*\(",
            r"/agents?\b",
        )

        for pattern in forbidden_required_syntax:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(content, re.compile(pattern))


if __name__ == "__main__":
    unittest.main()
