"""Contract tests for the canonical Patton Protocol package metadata."""

from pathlib import Path
import hashlib
import inspect
import os
import re
import subprocess
from tempfile import TemporaryDirectory
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PACKAGE_ROOT / "SKILL.md"
OPENAI_METADATA_PATH = PACKAGE_ROOT / "agents" / "openai.yaml"
MISSION_CONTRACT_PATH = PACKAGE_ROOT / "references" / "mission-contract.md"
WORKER_REPORT_PATH = PACKAGE_ROOT / "references" / "worker-report.md"
SAFETY_BUDGETS_PATH = PACKAGE_ROOT / "references" / "safety-and-budgets.md"
GITIGNORE_PATH = PACKAGE_ROOT / ".gitignore"
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
    "candidate tracked paths",
    "candidate untracked paths",
    "non-candidate untracked paths",
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
    "candidate tracked paths",
    "candidate untracked paths",
    "non-candidate untracked paths",
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
    "candidate_tracked_paths",
    "candidate_untracked_paths",
    "non_candidate_untracked_paths",
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
    "candidate_tracked_paths",
    "candidate_untracked_paths",
    "non_candidate_untracked_paths",
}
IDENTITY_KEYS = {"mission_id", "worker_role", "source_revision", "actor_id"}
ALLOWED_STATUSES = {"completed", "partial", "blocked", "failed"}
UNRESERVED_PATH_BYTES = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
UPPERCASE_HEX_DIGITS = frozenset("0123456789ABCDEF")


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


def canonical_path_token(root: Path, path: Path) -> bytes:
    """Encode path bytes without requiring valid UTF-8 filenames."""
    components = path.relative_to(root).parts
    encoded_components = []
    for component in components:
        raw_component = os.fsencode(component)
        encoded_components.append(
            b"".join(
                bytes((byte,)) if byte in UNRESERVED_PATH_BYTES else f"%{byte:02X}".encode("ascii")
                for byte in raw_component
            )
        )
    return b"/".join(encoded_components)


def decode_path_token(root: Path, token: str) -> Path:
    """Decode the ASCII path token used by explicit manifest entries."""
    components = []
    for component in token.split("/"):
        raw = bytearray()
        index = 0
        while index < len(component):
            if component[index] == "%":
                raw.append(int(component[index + 1 : index + 3], 16))
                index += 3
            else:
                raw.extend(component[index].encode("ascii"))
                index += 1
        components.append(os.fsdecode(bytes(raw)))
    return root.joinpath(*components)


def validate_path_token(root: Path, token: str) -> Path:
    """Decode one canonical, relative, lossless manifest path token."""
    if not isinstance(token, str) or not token or token.startswith("/"):
        raise ValueError("path token must be a nonempty relative token")
    if "\\" in token:
        raise ValueError("path token must use slash separators")
    components = token.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("path token contains an empty or traversal component")
    index = 0
    while index < len(token):
        character = token[index]
        if character == "/":
            index += 1
            continue
        if character == "%":
            if index + 2 >= len(token) or token[index + 1] not in UPPERCASE_HEX_DIGITS or token[index + 2] not in UPPERCASE_HEX_DIGITS:
                raise ValueError("path token has noncanonical percent encoding")
            byte = int(token[index + 1 : index + 3], 16)
            if byte in UNRESERVED_PATH_BYTES or byte in {0x00, 0x2F, 0x5C}:
                raise ValueError("path token has a noncanonical or encoded separator byte")
            index += 3
            continue
        if ord(character) > 0x7F or ord(character) not in UNRESERVED_PATH_BYTES:
            raise ValueError("path token contains a noncanonical literal byte")
        index += 1
    path = decode_path_token(root, token)
    try:
        canonical = canonical_path_token(root, path).decode("ascii")
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("path token is not canonical or stays within the root") from error
    if canonical != token:
        raise ValueError("path token is not canonical")
    return path


def canonical_fixture_digest(
    root: Path,
    *,
    tracked_paths: set[str] | None = None,
    post_mutation_tracked_paths: set[str] | None = None,
    candidate_untracked_paths: list[dict[str, object]] | None = None,
    executable_paths: set[str] | None = None,
    ignored_paths: set[str] | None = None,
    gitlink_paths: set[str] | None = None,
    behavior_affecting_untracked_paths: set[str] | None = None,
    non_candidate_untracked_paths: list[str] | None = None,
    discovered_nonignored_untracked_paths: set[str] | None = None,
) -> str:
    """Compute the deterministic digest described by the candidate contract."""
    records: list[bytes] = []
    candidate_untracked_paths = candidate_untracked_paths or []
    executable_paths = executable_paths or set()
    ignored_paths = ignored_paths or set()
    gitlink_paths = set(gitlink_paths or ())
    behavior_affecting_untracked_paths = set(behavior_affecting_untracked_paths or ())
    if non_candidate_untracked_paths is None:
        non_candidate_untracked_paths = []
    if not isinstance(non_candidate_untracked_paths, list):
        raise ValueError("non-candidate paths must be an ordered list")
    if any(not isinstance(token, str) for token in non_candidate_untracked_paths):
        raise ValueError("non-candidate paths must contain scalar path tokens")
    if len(set(non_candidate_untracked_paths)) != len(non_candidate_untracked_paths):
        raise ValueError("non-candidate paths contain a duplicate")
    if non_candidate_untracked_paths != sorted(non_candidate_untracked_paths):
        raise ValueError("non-candidate paths must use canonical sorted order")
    non_candidate_tokens = set(non_candidate_untracked_paths)
    discovered_nonignored_untracked_paths = (
        None
        if discovered_nonignored_untracked_paths is None
        else set(discovered_nonignored_untracked_paths)
    )
    tracked_paths = set(tracked_paths or ())
    tracked_tokens = set()
    for token in tracked_paths:
        validate_path_token(root, token)
        tracked_tokens.add(token)
    if post_mutation_tracked_paths is not None:
        post_mutation_tracked_tokens = set(post_mutation_tracked_paths)
        if post_mutation_tracked_tokens & gitlink_paths:
            raise ValueError("tracked inventory cannot contain gitlinks")
        expected_tracked_tokens = set()
        for token in post_mutation_tracked_tokens:
            path = validate_path_token(root, token)
            if path.is_symlink():
                raise ValueError("tracked inventory cannot contain symlinks")
            if path.is_file():
                expected_tracked_tokens.add(token)
        if tracked_tokens != expected_tracked_tokens:
            raise ValueError(
                "candidate_tracked_paths must equal the complete post-mutation tracked regular-file inventory"
            )
    for token in gitlink_paths:
        validate_path_token(root, token)
    for token in behavior_affecting_untracked_paths | non_candidate_tokens:
        validate_path_token(root, token)
    for token in discovered_nonignored_untracked_paths or ():
        validate_path_token(root, token)
    if behavior_affecting_untracked_paths & non_candidate_tokens:
        raise ValueError("untracked path has conflicting candidate classifications")
    if non_candidate_tokens & tracked_tokens:
        raise ValueError("tracked path cannot be classified as non-candidate output")
    if discovered_nonignored_untracked_paths is not None and discovered_nonignored_untracked_paths & tracked_tokens:
        raise ValueError("discovered untracked path overlaps a tracked path")
    if not executable_paths <= tracked_tokens:
        raise ValueError("executable metadata may only name tracked paths")
    untracked_tokens: set[str] = set()
    untracked_executable_paths: set[str] = set()
    for entry in candidate_untracked_paths:
        if set(entry) != {"path", "executable"}:
            raise ValueError("candidate untracked entry must contain path and executable")
        token = entry["path"]
        executable = entry["executable"]
        if not isinstance(token, str) or isinstance(executable, bool) or executable not in (0, 1):
            raise ValueError("candidate untracked entry has invalid path or executable flag")
        candidate_path = validate_path_token(root, token)
        if token in ignored_paths:
            raise ValueError("candidate untracked entry names an ignored output")
        if token in tracked_tokens:
            raise ValueError("candidate untracked entry overlaps a tracked path")
        if token in untracked_tokens:
            raise ValueError("candidate untracked paths contain a duplicate")
        untracked_tokens.add(token)
        if executable:
            untracked_executable_paths.add(token)
    if untracked_tokens & non_candidate_tokens:
        raise ValueError("untracked path has conflicting candidate classifications")
    manifest_paths = tracked_paths | untracked_tokens
    if gitlink_paths & manifest_paths:
        raise ValueError("gitlinks are not candidate files")
    ignored_behavior_paths = behavior_affecting_untracked_paths & ignored_paths
    if ignored_behavior_paths:
        raise ValueError(
            "ignored behavior-affecting untracked path blocks candidate lock; no inclusion schema exists"
        )
    omitted_behavior_paths = (
        behavior_affecting_untracked_paths - untracked_tokens - non_candidate_tokens
    )
    if omitted_behavior_paths:
        raise ValueError(
            "non-ignored behavior-affecting untracked path lacks candidate classification"
        )
    if discovered_nonignored_untracked_paths is not None:
        classified_untracked_paths = untracked_tokens | non_candidate_tokens
        if classified_untracked_paths != discovered_nonignored_untracked_paths:
            raise ValueError(
                "discovered non-ignored untracked paths must be classified exactly once"
            )
    paths = [validate_path_token(root, token) for token in manifest_paths]
    for path in sorted(paths, key=lambda candidate: canonical_path_token(root, candidate)):
        ancestor = path.parent
        while ancestor != root:
            if ancestor.is_symlink():
                raise ValueError(f"symlink ancestor is not a candidate path: {ancestor}")
            ancestor = ancestor.parent
        if path.is_symlink():
            raise ValueError(f"symlink is not a candidate file: {path}")
        relative_path = canonical_path_token(root, path)
        token = relative_path.decode("ascii")
        if token in tracked_tokens:
            executable = b"1" if token in executable_paths else b"0"
        else:
            executable = b"1" if token in untracked_executable_paths else b"0"
        payload = path.read_bytes()
        records.append(
            relative_path
            + b"\0"
            + executable
            + b"\0"
            + str(len(payload)).encode("ascii")
            + b"\0"
            + payload
        )
    digest = hashlib.sha256(b"".join(record + b"\n" for record in records)).hexdigest()
    return f"sha256:{digest}"


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
    def test_repository_ignores_python_bytecode_artifacts(self) -> None:
        content = read_text_or_empty(GITIGNORE_PATH)

        self.assertTrue(GITIGNORE_PATH.is_file())
        self.assertRegex(content, re.compile(r"(?m)^__pycache__/$"))

    def test_mission_contract_defines_required_fields(self) -> None:
        content = read_text_or_empty(MISSION_CONTRACT_PATH).lower()

        for field in MISSION_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, content)

    def test_mission_example_has_exact_required_keys(self) -> None:
        top_level, _ = parse_first_yaml_block(MISSION_CONTRACT_PATH)

        self.assertEqual(set(top_level), MISSION_ENVELOPE_KEYS)

    def test_verifier_mission_example_is_read_only(self) -> None:
        top_level, _ = parse_first_yaml_block(MISSION_CONTRACT_PATH)
        self.assertEqual(top_level.get("worker_role"), "verifier")
        self.assertEqual(top_level.get("allowed_paths"), "[]")

        content = " ".join(read_text_or_empty(MISSION_CONTRACT_PATH).lower().split())
        self.assertIn("verifier mission", content)
        self.assertRegex(
            content,
            re.compile(r"verifier.{0,180}read-only.{0,180}(cannot mutate|no candidate mutation)", re.IGNORECASE),
        )

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
        skill = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        for term in ("candidate_tracked_paths", "post-mutation state", "membership change"):
            with self.subTest(term=term):
                self.assertIn(term, skill)
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

    def test_candidate_tracked_paths_require_complete_post_mutation_inventory(self) -> None:
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split()).replace("`", "")
        for term in (
            "complete post-mutation",
            "tracked regular-file inventory",
            "named exclusions",
        ):
            with self.subTest(term=term):
                self.assertIn(term, safety)
        self.assertRegex(safety, re.compile(r"candidate_tracked_paths.{0,40}equals", re.IGNORECASE))

    def test_tracked_manifest_omission_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "kept.txt").write_bytes(b"kept\n")
            (root / "omitted.txt").write_bytes(b"omitted\n")

            with self.assertRaisesRegex(ValueError, "complete post-mutation"):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"kept.txt"},
                    post_mutation_tracked_paths={"kept.txt", "omitted.txt"},
                )

    def test_post_lock_tracked_membership_mutation_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "kept.txt").write_bytes(b"kept\n")
            locked_manifest = {"kept.txt"}
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths=locked_manifest,
                post_mutation_tracked_paths=locked_manifest,
            )
            (root / "added.txt").write_bytes(b"added\n")

            with self.assertRaisesRegex(ValueError, "complete post-mutation"):
                canonical_fixture_digest(
                    root,
                    tracked_paths=locked_manifest,
                    post_mutation_tracked_paths={"kept.txt", "added.txt"},
                )

        self.assertTrue(locked_digest.startswith("sha256:"))

    def test_fixture_digest_changes_after_post_build_mutation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "candidate.txt"
            source.parent.mkdir()
            source.write_bytes(b"candidate-v1\n")

            locked_digest = canonical_fixture_digest(root, tracked_paths={"src/candidate.txt"})
            source.write_bytes(b"candidate-v2\n")
            current_digest = canonical_fixture_digest(root, tracked_paths={"src/candidate.txt"})

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
            tracked_paths = {"src/candidate.txt", "config.ini"}

            locked_digest = canonical_fixture_digest(root, tracked_paths=tracked_paths)
            config.write_bytes(b"feature=new\n")
            current_digest = canonical_fixture_digest(root, tracked_paths=tracked_paths)

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
            "tracked paths",
            "candidate untracked paths",
            "unlisted path blocks candidate lock",
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

    def test_builder_outside_allowlist_requires_new_mission_handoff(self) -> None:
        allowed_paths = {"src/"}
        attempted_path = "config.ini"
        builder_report = {"status": "blocked", "files_changed": [attempted_path]}
        next_mission = {"mission_id": "mission-043", "handoff": "mission-042"}

        self.assertNotIn(attempted_path, allowed_paths)
        self.assertEqual(builder_report["status"], "blocked")
        self.assertNotEqual(next_mission["mission_id"], next_mission["handoff"])
        mission = " ".join(read_text_or_empty(MISSION_CONTRACT_PATH).lower().split())
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        for content in (mission, safety):
            self.assertIn("immutable", content)
            self.assertIn("new mission", content)
            self.assertIn("handoff", content)
        self.assertNotIn("unless patton prime expands the mission", mission)

    def test_phase_specific_candidate_envelopes_are_documented(self) -> None:
        mission = " ".join(read_text_or_empty(MISSION_CONTRACT_PATH).lower().split())
        report = " ".join(read_text_or_empty(WORKER_REPORT_PATH).lower().split())

        for content in (mission, report):
            for term in ("pre-candidate", "nullable", "prime authors", "before the verifier"):
                with self.subTest(content=content[:20], term=term):
                    self.assertIn(term, content)
        self.assertRegex(mission, re.compile(r"candidate_revision:\s*null", re.IGNORECASE))
        self.assertRegex(report, re.compile(r"content_digest:\s*null", re.IGNORECASE))

    def test_phase_flow_assigns_candidate_record_ownership(self) -> None:
        mission = " ".join(read_text_or_empty(MISSION_CONTRACT_PATH).lower().split())
        report = " ".join(read_text_or_empty(WORKER_REPORT_PATH).lower().split())

        for content in (mission, report):
            for term in ("builder terminal report", "prime authors", "verifier authors"):
                with self.subTest(content=content[:20], term=term):
                    self.assertIn(term, content)
        self.assertNotIn("builder copies the immutable values", report)
        self.assertNotIn("prime then copies the non-null", report)
        self.assertLess(report.index("builder terminal report"), report.index("prime authors"))
        self.assertLess(report.index("prime authors"), report.index("verifier authors"))

    def test_builder_and_verifier_are_separate_mission_cycles(self) -> None:
        skill = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        transitions = (
            "prime plans, dispatches, and observes builder mission",
            "builder terminal report",
            "prime reconciles builder report",
            "prime locks candidate",
            "prime plans, dispatches, and observes verifier mission",
            "verifier returns report",
            "prime observes and reconciles verifier report",
        )

        positions = []
        for transition in transitions:
            self.assertIn(transition, skill)
            positions.append(skill.index(transition))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("separate mission cycle", skill)
        self.assertNotIn("verifier reconcile", skill)

    def test_outer_lifecycle_maps_to_both_mission_cycles(self) -> None:
        skill = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        mapping = (
            "scope -> plan builder",
            "plan builder -> dispatch builder",
            "dispatch builder -> observe builder report",
            "observe builder report -> prime reconcile builder and lock candidate",
            "prime reconcile builder and lock candidate -> plan verifier",
            "plan verifier -> dispatch verifier",
            "dispatch verifier -> observe verifier live checks and monitor timeout and budget",
            "observe verifier live checks and monitor timeout and budget -> verify verifier checks",
            "verify verifier checks -> verifier report",
            "verifier report -> prime observe and reconcile verifier",
            "prime observe and reconcile verifier -> final report",
        )

        self.assertIn("outer lifecycle", skill)
        self.assertIn("mission-family phases", skill)
        positions = []
        for transition in mapping:
            self.assertIn(transition, skill)
            positions.append(skill.index(transition))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("both cycles", skill)

    def test_verifier_live_observation_precedes_terminal_report(self) -> None:
        skill = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        transitions = (
            "dispatch verifier",
            "observe verifier live checks and monitor timeout and budget",
            "verifier report",
            "prime observe and reconcile verifier",
            "final report",
        )

        positions = []
        for transition in transitions:
            self.assertIn(transition, skill)
            positions.append(skill.index(transition))
        self.assertEqual(positions, sorted(positions))
        self.assertRegex(
            skill,
            re.compile(
                r"observe verifier live checks and monitor timeout and budget"
                r".{0,200}before verifier report",
                re.IGNORECASE,
            ),
        )

    def test_live_observe_wraps_long_running_verifier_check(self) -> None:
        skill = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        for term in (
            "live observe wraps verifier checks",
            "runs concurrently with them",
            "before the verifier report",
            "prime alone stops the mission",
        ):
            with self.subTest(term=term):
                self.assertIn(term, skill)

        events = ["dispatch verifier", "verifier check started"]
        events.append("live observe wraps verifier checks")
        events.append("timeout signal")
        events.append("prime alone stops the mission")

        self.assertLess(events.index("verifier check started"), events.index("timeout signal"))
        self.assertLess(events.index("timeout signal"), events.index("prime alone stops the mission"))
        self.assertNotIn("verifier report", events)

    def test_serial_observe_requires_host_enforced_bound_for_blocking_check(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
                + read_text_or_empty(WORKER_REPORT_PATH)
            ).lower().split()
        )
        for term in (
            "start observe",
            "run verify while observe is active",
            "stop observe",
            "host-enforced deadline",
            "command timeout",
            "cannot enforce the bound safely",
            "mission remains `blocked`",
            "no late terminal report is accepted",
        ):
            with self.subTest(term=term):
                self.assertIn(term, content)

        host_capabilities = {
            "deadline": {"can_spawn_workers": False, "host_enforced_deadline": True},
            "command_timeout": {"can_spawn_workers": False, "command_timeout": True},
            "unbounded": {"can_spawn_workers": False},
        }

        def serial_cycle(capabilities: dict[str, bool], blocking_check: object) -> dict[str, object]:
            events = ["start observe"]
            bounded = capabilities.get("host_enforced_deadline", False) or capabilities.get("command_timeout", False)
            if not bounded:
                events.append("mission remains `blocked`")
                return {"events": events, "status": "blocked", "late_report_accepted": False}
            events.append("run verify while observe is active")
            try:
                blocking_check()
            except TimeoutError:
                events.append("timeout enforced by host")
            events.append("stop observe")
            events.append("report")
            return {"events": events, "status": "partial", "late_report_accepted": False}

        for capability_name in ("deadline", "command_timeout"):
            with self.subTest(capability=capability_name):
                check_calls = [0]

                def blocking_check() -> None:
                    check_calls[0] += 1
                    raise TimeoutError

                result = serial_cycle(host_capabilities[capability_name], blocking_check)
                events = result["events"]
                self.assertEqual(check_calls[0], 1)
                self.assertEqual(result["late_report_accepted"], False)
                self.assertEqual(
                    [events.index(term) for term in ("start observe", "run verify while observe is active", "stop observe", "report")],
                    sorted(events.index(term) for term in ("start observe", "run verify while observe is active", "stop observe", "report")),
                )

        check_calls = [0]

        def unbounded_blocking_check() -> None:
            check_calls[0] += 1
            raise AssertionError("unsafe host must not run an unbounded check")

        unbounded = serial_cycle(host_capabilities["unbounded"], unbounded_blocking_check)
        self.assertEqual(unbounded["status"], "blocked")
        self.assertEqual(unbounded["late_report_accepted"], False)
        self.assertEqual(check_calls[0], 0)
        self.assertNotIn("report", unbounded["events"])

    def test_quartermaster_signals_ceiling_but_prime_alone_stops(self) -> None:
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        normative = (
            read_text_or_empty(SKILL_PATH)
            + read_text_or_empty(SAFETY_BUDGETS_PATH)
        ).lower()

        self.assertRegex(
            safety,
            re.compile(
                r"quartermaster records.{0,100}signals? (?:the )?(?:budget|timeout|mission)"
                r" (?:ceiling|limit)",
                re.IGNORECASE,
            ),
        )
        self.assertIn("prime solely stops the mission", safety)
        for line in normative.splitlines():
            if "quartermaster" in line:
                quartermaster_clause = line.lower().split("quartermaster", 1)[1].split(";", 1)[0]
                self.assertNotRegex(quartermaster_clause, re.compile(r"\bstops?\b", re.IGNORECASE))

    def test_skill_owns_the_only_normative_lifecycle_sequence(self) -> None:
        skill = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        references = (
            MISSION_CONTRACT_PATH,
            WORKER_REPORT_PATH,
            SAFETY_BUDGETS_PATH,
        )

        self.assertEqual(skill.count("sole normative source for the canonical lifecycle sequence"), 1)
        for reference in references:
            content = " ".join(read_text_or_empty(reference).lower().split())
            with self.subTest(reference=reference.name):
                self.assertNotIn("sole normative source for the canonical lifecycle sequence", content)
                self.assertNotIn("cycles in this canonical order", content)
                self.assertIn("../skill.md#lifecycle", content)
                self.assertNotIn("prime plans, dispatches, and observes the builder mission", content)

    def test_gitlinks_are_rejected_from_candidate_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            gitlink = root / "submodule"
            gitlink.mkdir()

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"submodule"},
                    gitlink_paths={"submodule"},
                )

        content = " ".join(
            (
                read_text_or_empty(MISSION_CONTRACT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        self.assertRegex(content, re.compile(r"gitlink.{0,160}(reject|excluded|not serialized)", re.IGNORECASE))

    def test_real_git_tracked_symlinks_are_rejected_before_manifest_selection(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Protocol Test"], cwd=root, check=True)
            (root / "tracked.txt").write_bytes(b"tracked\n")
            target_directory = root / "untracked-target"
            target_directory.mkdir()
            (target_directory / "target.txt").write_bytes(b"target\n")
            try:
                (root / "directory-link").symlink_to(target_directory, target_is_directory=True)
                (root / "dangling-link").symlink_to("missing-target")
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink fixture unavailable: {error}")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "symlinks"], cwd=root, check=True)
            tracked_entries = set(
                subprocess.run(
                    ["git", "ls-files"], cwd=root, check=True, text=True, capture_output=True
                ).stdout.splitlines()
            )

            with self.assertRaisesRegex(ValueError, "tracked inventory"):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"tracked.txt"},
                    post_mutation_tracked_paths=tracked_entries,
                )

        self.assertIn("directory-link", tracked_entries)
        self.assertIn("dangling-link", tracked_entries)

    def test_real_git_tracked_gitlink_is_rejected_before_manifest_selection(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            child = root / "child"
            parent = root / "parent"
            child.mkdir()
            parent.mkdir()
            for repository in (child, parent):
                subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
                subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
                subprocess.run(["git", "config", "user.name", "Protocol Test"], cwd=repository, check=True)
            (child / "module.txt").write_bytes(b"module\n")
            subprocess.run(["git", "add", "module.txt"], cwd=child, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "module"], cwd=child, check=True)
            (parent / "tracked.txt").write_bytes(b"tracked\n")
            subprocess.run(["git", "add", "tracked.txt"], cwd=parent, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "parent"], cwd=parent, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "submodule",
                    "add",
                    str(child),
                    "vendor/child",
                ],
                cwd=parent,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            subprocess.run(["git", "commit", "--quiet", "-m", "submodule"], cwd=parent, check=True)
            tracked_entries = set(
                subprocess.run(
                    ["git", "ls-files"], cwd=parent, check=True, text=True, capture_output=True
                ).stdout.splitlines()
            )
            gitlink_entries = {
                line.split()[3]
                for line in subprocess.run(
                    ["git", "ls-files", "-s"], cwd=parent, check=True, text=True, capture_output=True
                ).stdout.splitlines()
                if line.startswith("160000 ")
            }

            with self.assertRaisesRegex(ValueError, "gitlink"):
                canonical_fixture_digest(
                    parent,
                    tracked_paths={"tracked.txt", ".gitmodules"},
                    post_mutation_tracked_paths=tracked_entries,
                    gitlink_paths=gitlink_entries,
                )

        self.assertEqual(gitlink_entries, {"vendor/child"})

    def test_untracked_behavior_input_omission_blocks_candidate_lock(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entrypoint = root / "main.py"
            module = root / "helper.py"
            entrypoint.write_bytes(b"import helper\nprint(helper.value)\n")
            module.write_bytes(b"value = 1\n")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"main.py"},
                    discovered_nonignored_untracked_paths={"helper.py"},
                )

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"main.py"},
                    discovered_nonignored_untracked_paths={"helper.py"},
                    behavior_affecting_untracked_paths={"helper.py"},
                )

            output = root / "artifact.log"
            output.write_bytes(b"diagnostic\n")
            classified_output = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                non_candidate_untracked_paths=["artifact.log"],
                discovered_nonignored_untracked_paths={"artifact.log"},
            )

        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(MISSION_CONTRACT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        for term in (
            "non-ignored untracked",
            "candidate_untracked_paths",
            "non_candidate_untracked_paths",
            "proven not to affect",
            "blocks candidate lock",
        ):
            with self.subTest(term=term):
                self.assertIn(term, content)
        self.assertRegex(classified_output, re.compile(r"^sha256:[0-9a-f]{64}$"))

    def test_behavior_inputs_are_reserved_for_candidate_untracked_paths(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(MISSION_CONTRACT_PATH)
                + read_text_or_empty(WORKER_REPORT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        self.assertRegex(
            content,
            re.compile(
                r"every non-ignored untracked path that can affect the objective"
                r".{0,240}candidate_untracked_paths",
                re.IGNORECASE,
            ),
        )
        self.assertRegex(
            content,
            re.compile(
                r"non_candidate_untracked_paths.{0,180}reserved for paths proven not to affect",
                re.IGNORECASE,
            ),
        )
        self.assertRegex(
            content,
            re.compile(
                r"non-candidate classification of a behavior input.{0,100}blocks candidate lock",
                re.IGNORECASE,
            ),
        )

    def test_behavior_input_mutation_changes_candidate_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entrypoint = root / "main.py"
            module = root / "helper.py"
            entrypoint.write_bytes(b"import helper\nprint(helper.value)\n")
            module.write_bytes(b"value = 1\n")
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                candidate_untracked_paths=[{"path": "helper.py", "executable": 0}],
                behavior_affecting_untracked_paths={"helper.py"},
                discovered_nonignored_untracked_paths={"helper.py"},
            )
            module.write_bytes(b"value = 2\n")
            current_digest = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                candidate_untracked_paths=[{"path": "helper.py", "executable": 0}],
                behavior_affecting_untracked_paths={"helper.py"},
                discovered_nonignored_untracked_paths={"helper.py"},
            )

        self.assertNotEqual(locked_digest, current_digest)

    def test_untracked_behavior_input_inclusion_and_post_lock_mutation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entrypoint = root / "main.py"
            module = root / "helper.py"
            entrypoint.write_bytes(b"import helper\nprint(helper.value)\n")
            module.write_bytes(b"value = 1\n")
            without_module = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
            )
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                candidate_untracked_paths=[{"path": "helper.py", "executable": 0}],
                behavior_affecting_untracked_paths={"helper.py"},
                discovered_nonignored_untracked_paths={"helper.py"},
            )
            module.write_bytes(b"value = 2\n")
            current_digest = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                candidate_untracked_paths=[{"path": "helper.py", "executable": 0}],
                behavior_affecting_untracked_paths={"helper.py"},
                discovered_nonignored_untracked_paths={"helper.py"},
            )

        self.assertNotEqual(without_module, locked_digest)
        self.assertNotEqual(locked_digest, current_digest)
        self.assertNotEqual(
            {"content_digest": locked_digest},
            {"content_digest": current_digest},
            "post-lock mutation makes prior behavior-input evidence stale",
        )

    def test_ignored_behavior_input_blocks_lock_but_generated_output_stays_excluded(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            entrypoint = root / "main.py"
            hidden_module = root / "hidden_module.py"
            generated_output = root / "build" / "cache.bin"
            entrypoint.write_bytes(b"import hidden_module\nprint(hidden_module.value)\n")
            hidden_module.write_bytes(b"value = 1\n")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"main.py"},
                    ignored_paths={"hidden_module.py"},
                    behavior_affecting_untracked_paths={"hidden_module.py"},
                )

            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                ignored_paths={"build/cache.bin"},
            )
            generated_output.parent.mkdir()
            generated_output.write_bytes(b"generated\n")
            current_digest = canonical_fixture_digest(
                root,
                tracked_paths={"main.py"},
                ignored_paths={"build/cache.bin"},
            )

        self.assertEqual(locked_digest, current_digest)
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(MISSION_CONTRACT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        self.assertIn(
            "this schema has no inclusion form for ignored behavior inputs, so prime blocks candidate lock until a future schema defines one",
            content,
        )

    def test_non_candidate_paths_require_ordered_unique_scalar_tokens(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for paths in (["output/z.log", "output/a.log"], ["output/a.log", "output/a.log"]):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    canonical_fixture_digest(root, non_candidate_untracked_paths=paths)

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    non_candidate_untracked_paths=[{"path": "output.log", "executable": 0}],  # type: ignore[list-item]
                )

    def test_candidate_and_non_candidate_paths_cannot_overlap(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate = root / "config.ini"
            candidate.write_bytes(b"feature=on\n")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    candidate_untracked_paths=[{"path": "config.ini", "executable": 0}],
                    non_candidate_untracked_paths=["config.ini"],
                    discovered_nonignored_untracked_paths={"config.ini"},
                )

    def test_non_candidate_schema_uses_scalar_path_tokens(self) -> None:
        content = read_text_or_empty(MISSION_CONTRACT_PATH)
        start = content.index("### Non-candidate untracked paths")
        section = content[start : content.find("### Stopping condition", start)]
        section = " ".join(section.split())

        self.assertRegex(section, re.compile(r"sorted list of unique scalar lossless path tokens", re.IGNORECASE))
        self.assertNotIn("executable", section.lower())

    def test_ignore_inputs_are_repository_controlled_and_config_invariant(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(MISSION_CONTRACT_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        for term in (
            "repository-controlled ignore rules",
            "clone-local",
            "user-global",
            "do not affect the candidate manifest",
        ):
            with self.subTest(term=term):
                self.assertIn(term, content)

        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate = root / "config.ini"
            candidate.write_bytes(b"feature=on\n")

            def digest_with_ignore_inputs(
                repository_rules: set[str],
                clone_local_rules: set[str],
                user_global_rules: set[str],
            ) -> str:
                del clone_local_rules, user_global_rules
                return canonical_fixture_digest(
                    root,
                    candidate_untracked_paths=[{"path": "config.ini", "executable": 0}],
                    ignored_paths=repository_rules,
                )

            baseline = digest_with_ignore_inputs(set(), set(), set())
            ambient_variance = digest_with_ignore_inputs(
                set(),
                {"config.ini"},
                {"config.ini"},
            )
            with self.assertRaises(ValueError):
                digest_with_ignore_inputs({"config.ini"}, set(), set())

        self.assertEqual(baseline, ambient_variance)

    def test_post_lock_mutation_scope_names_locked_manifest_state(self) -> None:
        content = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())

        self.assertRegex(
            content,
            re.compile(
                r"post-lock.{0,240}locked (?:candidate )?manifest.{0,240}"
                r"(?:membership|bytes).{0,160}(?:mode|executable)",
                re.IGNORECASE,
            ),
        )
        self.assertRegex(
            content,
            re.compile(
                r"post-lock.{0,220}new non-ignored untracked.{0,220}"
                r"pending classification",
                re.IGNORECASE,
            ),
        )

    def test_generated_exclusion_uses_manifest_and_ignore_state(self) -> None:
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        helper = inspect.getsource(canonical_fixture_digest)

        for term in ("explicit manifest", "source-control ignore state", "unlisted path blocks candidate lock"):
            with self.subTest(term=term):
                self.assertIn(term, safety)
        self.assertNotIn("GENERATED_DIRECTORY_NAMES", helper)
        self.assertNotIn("GENERATED_SUFFIXES", helper)

    def test_manifest_uses_post_mutation_git_state_not_source_revision(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Protocol Test"], cwd=root, check=True)
            kept = root / "kept.txt"
            removed = root / "removed.txt"
            kept.write_bytes(b"kept\n")
            removed.write_bytes(b"removed\n")
            subprocess.run(["git", "add", "kept.txt", "removed.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "source"], cwd=root, check=True)

            source_manifest = set(
                subprocess.run(
                    ["git", "ls-files"], cwd=root, check=True, text=True, capture_output=True
                ).stdout.splitlines()
            )
            locked_digest = canonical_fixture_digest(root, tracked_paths=source_manifest)
            removed.unlink()
            added = root / "added.txt"
            added.write_bytes(b"added\n")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            candidate_manifest = set(
                subprocess.run(
                    ["git", "ls-files"], cwd=root, check=True, text=True, capture_output=True
                ).stdout.splitlines()
            )
            current_digest = canonical_fixture_digest(root, tracked_paths=candidate_manifest)

        self.assertEqual(source_manifest, {"kept.txt", "removed.txt"})
        self.assertEqual(candidate_manifest, {"added.txt", "kept.txt"})
        self.assertNotEqual(locked_digest, current_digest)
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        self.assertIn("post-mutation source-control state", safety)
        self.assertNotRegex(safety, re.compile(r"tracked files from the source-control state at `source_revision`", re.IGNORECASE))

    def test_portable_executable_semantics_contribute_to_candidate_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "candidate.txt"
            source.write_bytes(b"same bytes\n")
            locked_digest = canonical_fixture_digest(root, tracked_paths={"candidate.txt"}, executable_paths=set())
            executable_digest = canonical_fixture_digest(root, tracked_paths={"candidate.txt"}, executable_paths={"candidate.txt"})

        self.assertNotEqual(locked_digest, executable_digest)

    def test_generated_artifact_does_not_change_tracked_manifest_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "candidate.txt"
            generated = root / "__pycache__" / "candidate.cpython-312.pyc"
            source.parent.mkdir()
            source.write_bytes(b"candidate\n")
            tracked_paths = {"src/candidate.txt"}
            ignored_paths = {"__pycache__/candidate.cpython-312.pyc"}
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths=tracked_paths,
                ignored_paths=ignored_paths,
                discovered_nonignored_untracked_paths=set(),
            )
            generated.parent.mkdir()
            generated.write_bytes(b"generated\n")
            current_digest = canonical_fixture_digest(
                root,
                tracked_paths=tracked_paths,
                ignored_paths=ignored_paths,
                discovered_nonignored_untracked_paths=set(),
            )

        self.assertEqual(locked_digest, current_digest)

    def test_nonignored_post_lock_addition_requires_classification(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "src" / "candidate.txt"
            source.parent.mkdir()
            source.write_bytes(b"candidate\n")
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths={"src/candidate.txt"},
                discovered_nonignored_untracked_paths=set(),
            )
            added = root / "new-output.log"
            added.write_bytes(b"new\n")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths={"src/candidate.txt"},
                    discovered_nonignored_untracked_paths={"new-output.log"},
                )

            classified_digest = canonical_fixture_digest(
                root,
                tracked_paths={"src/candidate.txt"},
                non_candidate_untracked_paths=["new-output.log"],
                discovered_nonignored_untracked_paths={"new-output.log"},
            )

        self.assertEqual(locked_digest, classified_digest)
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        self.assertRegex(
            content,
            re.compile(
                r"post-lock.{0,220}new non-ignored untracked.{0,220}(classification|stale|invalid)",
                re.IGNORECASE,
            ),
        )

    def test_delegation_decision_table_exempts_mandatory_verifier(self) -> None:
        content = " ".join(
            (
                read_text_or_empty(SKILL_PATH)
                + read_text_or_empty(SAFETY_BUDGETS_PATH)
            ).lower().split()
        )
        decisions = {
            "one builder + mandatory independent read-only verifier": r"one builder.{0,180}mandatory independent read-only verifier.{0,220}(exempt|does not apply|always|required)",
            "parallel builders": r"parallel builders.{0,220}(independent|separate mutable paths).{0,220}(delegate|parallel)",
            "overlapping mutable paths": r"overlapping mutable paths.{0,220}(serial|serialize)",
            "no-spawn host": r"no-spawn host.{0,220}serial fallback",
        }
        for decision, pattern in decisions.items():
            with self.subTest(decision=decision):
                self.assertRegex(content, re.compile(pattern, re.IGNORECASE))

    def test_tracked_deletion_changes_post_mutation_manifest_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            kept = root / "kept.txt"
            deleted = root / "deleted.txt"
            kept.write_bytes(b"kept\n")
            deleted.write_bytes(b"deleted\n")
            source_manifest = {"kept.txt", "deleted.txt"}
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths=source_manifest,
                post_mutation_tracked_paths=source_manifest,
            )
            deleted.unlink()
            candidate_manifest = {"kept.txt"}
            current_digest = canonical_fixture_digest(
                root,
                tracked_paths=candidate_manifest,
                post_mutation_tracked_paths=source_manifest,
            )

        self.assertNotEqual(locked_digest, current_digest)

    def test_tracked_addition_changes_post_mutation_manifest_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            kept = root / "kept.txt"
            added = root / "added.txt"
            kept.write_bytes(b"kept\n")
            source_manifest = {"kept.txt"}
            locked_digest = canonical_fixture_digest(
                root,
                tracked_paths=source_manifest,
                post_mutation_tracked_paths=source_manifest,
            )
            added.write_bytes(b"added\n")
            candidate_manifest = {"kept.txt", "added.txt"}
            current_digest = canonical_fixture_digest(
                root,
                tracked_paths=candidate_manifest,
                post_mutation_tracked_paths=candidate_manifest,
            )

        self.assertNotEqual(locked_digest, current_digest)

    def test_post_lock_manifest_membership_change_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            kept = root / "kept.txt"
            added = root / "added.txt"
            kept.write_bytes(b"kept\n")
            locked_manifest = {"kept.txt"}
            locked_digest = canonical_fixture_digest(root, tracked_paths=locked_manifest)
            added.write_bytes(b"added\n")
            current_manifest = {"kept.txt", "added.txt"}
            current_digest = canonical_fixture_digest(root, tracked_paths=current_manifest)

        self.assertNotEqual(locked_manifest, current_manifest)
        self.assertNotEqual(locked_digest, current_digest)
        content = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        self.assertRegex(content, re.compile(r"manifest.{0,180}(lock|freeze).{0,180}(membership|reject)", re.IGNORECASE))

    def test_candidate_untracked_entry_schema_uses_lossless_path_and_flag(self) -> None:
        content = " ".join(read_text_or_empty(MISSION_CONTRACT_PATH).lower().split())

        for term in ("candidate untracked paths", "path: lossless token", "executable: 0 | 1"):
            with self.subTest(term=term):
                self.assertIn(term, content)
        self.assertRegex(content, re.compile(r"candidate_untracked_paths.{0,240}path:.{0,100}executable", re.IGNORECASE))

    def test_explicit_untracked_manifest_entry_contributes_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config.ini"
            config.write_bytes(b"feature=on\n")
            without_config = canonical_fixture_digest(root, tracked_paths=set())
            with_config = canonical_fixture_digest(
                root,
                tracked_paths=set(),
                candidate_untracked_paths=[{"path": "config.ini", "executable": 0}],
            )

        self.assertNotEqual(without_config, with_config)

    def test_explicit_ignored_untracked_entry_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            ignored = root / "ignored-output.bin"
            ignored.write_bytes(b"ignored\n")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths=set(),
                    candidate_untracked_paths=[{"path": "ignored-output.bin", "executable": 0}],
                    ignored_paths={"ignored-output.bin"},
                )

    def test_explicit_generated_looking_nonignored_entry_is_included(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            generated_looking = root / "build" / "output.pyc"
            generated_looking.parent.mkdir()
            generated_looking.write_bytes(b"candidate\n")
            without_entry = canonical_fixture_digest(root, tracked_paths=set())
            with_entry = canonical_fixture_digest(
                root,
                tracked_paths=set(),
                candidate_untracked_paths=[{"path": "build/output.pyc", "executable": 0}],
                ignored_paths=set(),
            )

        self.assertNotEqual(without_entry, with_entry)

    def test_untracked_path_token_rejects_unsafe_or_noncanonical_forms(self) -> None:
        invalid_tokens = (
            "../outside.txt",
            "/absolute.txt",
            "src/../candidate.txt",
            "src/%2Fcandidate.txt",
            "src/%5Ccandidate.txt",
            "src/%41.txt",
            "src/%aa.txt",
        )
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for token in invalid_tokens:
                with self.subTest(token=token), self.assertRaises(ValueError):
                    canonical_fixture_digest(
                        root,
                        tracked_paths=set(),
                        candidate_untracked_paths=[{"path": token, "executable": 0}],
                    )

    def test_untracked_path_token_rejects_duplicates_and_tracked_overlap(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config.ini"
            config.write_bytes(b"feature=on\n")
            duplicate = [{"path": "config.ini", "executable": 0}, {"path": "config.ini", "executable": 1}]
            overlap = [{"path": "config.ini", "executable": 0}]

            with self.assertRaises(ValueError):
                canonical_fixture_digest(root, candidate_untracked_paths=duplicate)
            with self.assertRaises(ValueError):
                canonical_fixture_digest(root, tracked_paths={"config.ini"}, candidate_untracked_paths=overlap)

    def test_tracked_executable_metadata_is_preserved_with_untracked_entries(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            tracked = root / "tracked.sh"
            config = root / "config.ini"
            tracked.write_bytes(b"#!/bin/sh\n")
            config.write_bytes(b"feature=on\n")
            not_executable = canonical_fixture_digest(
                root,
                tracked_paths={"tracked.sh"},
                executable_paths=set(),
                candidate_untracked_paths=[{"path": "config.ini", "executable": 0}],
            )
            executable = canonical_fixture_digest(
                root,
                tracked_paths={"tracked.sh"},
                executable_paths={"tracked.sh"},
                candidate_untracked_paths=[{"path": "config.ini", "executable": 0}],
            )

        self.assertNotEqual(not_executable, executable)

    def test_untracked_executable_flag_cannot_be_overridden_by_tracked_metadata(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            config = root / "config.ini"
            config.write_bytes(b"feature=on\n")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths=set(),
                    executable_paths={"config.ini"},
                    candidate_untracked_paths=[{"path": "config.ini", "executable": 0}],
                )

    @unittest.skipIf(os.name == "nt", "requires a POSIX byte filename fixture")
    def test_non_utf8_filename_has_lossless_digest_path_token(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_name = b"config-\xff.ini"
            path = root / os.fsdecode(raw_name)
            path.write_bytes(b"feature=on\n")
            digest = canonical_fixture_digest(root, tracked_paths={canonical_path_token(root, path).decode("ascii")})

        self.assertRegex(digest, re.compile(r"^sha256:[0-9a-f]{64}$"))

    def test_symlink_in_candidate_manifest_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.txt"
            link = root / "link.txt"
            target.write_bytes(b"target\n")
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink fixture unavailable: {error}")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(root, tracked_paths={"link.txt"})

    def test_excluded_generated_symlink_does_not_enter_manifest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.txt"
            generated = root / "__pycache__"
            link = generated / "link.pyc"
            target.write_bytes(b"target\n")
            ignored_paths = {"__pycache__/link.pyc"}
            locked_digest = canonical_fixture_digest(root, tracked_paths={"target.txt"}, ignored_paths=ignored_paths)
            generated.mkdir()
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink fixture unavailable: {error}")

            current_digest = canonical_fixture_digest(root, tracked_paths={"target.txt"}, ignored_paths=ignored_paths)

        self.assertEqual(locked_digest, current_digest)

    def test_explicit_untracked_symlink_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.txt"
            link = root / "link.txt"
            target.write_bytes(b"target\n")
            try:
                link.symlink_to(target)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink fixture unavailable: {error}")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(
                    root,
                    tracked_paths=set(),
                    candidate_untracked_paths=[{"path": "link.txt", "executable": 0}],
                )

    def test_symlink_ancestor_in_candidate_manifest_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target_directory = root / "target"
            link_directory = root / "link"
            target_file = target_directory / "candidate.txt"
            target_directory.mkdir()
            target_file.write_bytes(b"candidate\n")
            try:
                link_directory.symlink_to(target_directory, target_is_directory=True)
            except (NotImplementedError, OSError) as error:
                self.skipTest(f"symlink fixture unavailable: {error}")

            with self.assertRaises(ValueError):
                canonical_fixture_digest(root, tracked_paths={"link/candidate.txt"})

    def test_executable_metadata_serialization_is_portable(self) -> None:
        content = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())

        self.assertIn("portable executable semantics", content)
        self.assertIn("executable flag", content)
        self.assertRegex(content, re.compile(r"host-neutral|platform-neutral", re.IGNORECASE))

    def test_digest_returns_prefixed_known_answer_for_empty_snapshot(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            digest = canonical_fixture_digest(Path(temporary_directory), tracked_paths=set())

        self.assertEqual(digest, "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_candidate_revision_fallback_is_digest_prefixed_known_answer(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            digest = canonical_fixture_digest(Path(temporary_directory), tracked_paths=set())
            candidate_revision = "candidate:" + digest

        self.assertEqual(
            candidate_revision,
            "candidate:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        self.assertIn('candidate_revision = "candidate:" + content_digest', safety)

    def test_digest_returns_known_answer_for_one_file_snapshot(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate = root / "a.txt"
            candidate.write_bytes(b"hello\n")
            digest = canonical_fixture_digest(root, tracked_paths={"a.txt"})

        self.assertEqual(digest, "sha256:8b5a61dc4a507ede77fb4e38bb438aa8008acd8893ad23d86d7055fa16946562")

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

    def test_inconclusive_conflict_human_decisions_keep_candidate_blocked(self) -> None:
        content = " ".join(read_text_or_empty(SKILL_PATH).lower().split())
        sentences = re.split(r"(?<=[.!?])\s+", content)
        expected_states = {
            "recovery": ("blocked", "unverified"),
            "risk acceptance": ("blocked", "unverified", "irreversible gate", "closed"),
            "abandonment": ("blocked", "unverified"),
        }

        for decision, states in expected_states.items():
            for state in states:
                with self.subTest(decision=decision, state=state):
                    self.assertTrue(
                        any(
                            decision in sentence and (f"`{state}`" in sentence or state in sentence)
                            for sentence in sentences
                        ),
                        f"{decision} must preserve {state}",
                    )

        human_candidate_verification_prohibition = re.compile(
            r"\bhuman\b.{0,180}\b(?:cannot|may not)\b"
            r".{0,120}\b(?:mark|claim)\b.{0,120}\binconclusive\b"
            r".{0,120}\bverified\b",
            re.IGNORECASE,
        )
        human_irreversible_prohibition = re.compile(
            r"\bhuman\b.{0,220}\b(?:cannot|may not)\b"
            r"(?:(?!\b(?:can|may|will|shall|must|cannot|may not)\b).){0,180}"
            r"\b(?:authorize|approve|approval)\b"
            r".{0,100}\birreversible action\b",
            re.IGNORECASE,
        )
        prohibition_rules = {
            "candidate verification": (
                human_candidate_verification_prohibition,
                "a human cannot mark an inconclusive candidate verified",
                "a human may mark an inconclusive candidate verified",
            ),
            "irreversible action": (
                human_irreversible_prohibition,
                "or authorize an irreversible action",
                "or may authorize an irreversible action",
            ),
        }
        for source_path in (SKILL_PATH, SAFETY_BUDGETS_PATH):
            source = " ".join(read_text_or_empty(source_path).lower().split())
            for rule, (pattern, original, reversed_clause) in prohibition_rules.items():
                with self.subTest(source=source_path.name, rule=rule):
                    self.assertRegex(source, pattern)

                mutated_source = source.replace(original, reversed_clause, 1)
                with self.subTest(source=source_path.name, rule=rule, mutation="reversed"):
                    self.assertNotEqual(source, mutated_source)
                    self.assertNotRegex(mutated_source, pattern)

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
