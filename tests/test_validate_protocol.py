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
    candidate_untracked_paths: list[dict[str, object]] | None = None,
    executable_paths: set[str] | None = None,
    ignored_paths: set[str] | None = None,
) -> str:
    """Compute the deterministic digest described by the candidate contract."""
    records: list[bytes] = []
    candidate_untracked_paths = candidate_untracked_paths or []
    executable_paths = executable_paths or set()
    ignored_paths = ignored_paths or set()
    tracked_paths = set(tracked_paths or ())
    tracked_tokens = set()
    for token in tracked_paths:
        validate_path_token(root, token)
        tracked_tokens.add(token)
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
    manifest_paths = tracked_paths | untracked_tokens
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
            "untracked paths not listed",
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
            "prime reconciles verifier report",
        )

        positions = []
        for transition in transitions:
            self.assertIn(transition, skill)
            positions.append(skill.index(transition))
        self.assertEqual(positions, sorted(positions))
        self.assertIn("separate mission cycle", skill)
        self.assertNotIn("verifier reconcile", skill)

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
                r"unlisted generated.{0,160}(?:outside|outside the).{0,160}"
                r"(?:does not|do not).{0,80}(?:stale|identity)",
                re.IGNORECASE,
            ),
        )

    def test_generated_exclusion_uses_manifest_and_ignore_state(self) -> None:
        safety = " ".join(read_text_or_empty(SAFETY_BUDGETS_PATH).lower().split())
        helper = inspect.getsource(canonical_fixture_digest)

        for term in ("explicit manifest", "source-control ignore state", "untracked paths not listed"):
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
            locked_digest = canonical_fixture_digest(root, tracked_paths=tracked_paths)
            generated.parent.mkdir()
            generated.write_bytes(b"generated\n")
            current_digest = canonical_fixture_digest(root, tracked_paths=tracked_paths)

        self.assertEqual(locked_digest, current_digest)

    def test_tracked_deletion_changes_post_mutation_manifest_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            kept = root / "kept.txt"
            deleted = root / "deleted.txt"
            kept.write_bytes(b"kept\n")
            deleted.write_bytes(b"deleted\n")
            source_manifest = {"kept.txt", "deleted.txt"}
            locked_digest = canonical_fixture_digest(root, tracked_paths=source_manifest)
            deleted.unlink()
            candidate_manifest = {"kept.txt"}
            current_digest = canonical_fixture_digest(root, tracked_paths=candidate_manifest)

        self.assertNotEqual(locked_digest, current_digest)

    def test_tracked_addition_changes_post_mutation_manifest_digest(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            kept = root / "kept.txt"
            added = root / "added.txt"
            kept.write_bytes(b"kept\n")
            source_manifest = {"kept.txt"}
            locked_digest = canonical_fixture_digest(root, tracked_paths=source_manifest)
            added.write_bytes(b"added\n")
            candidate_manifest = {"kept.txt", "added.txt"}
            current_digest = canonical_fixture_digest(root, tracked_paths=candidate_manifest)

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
