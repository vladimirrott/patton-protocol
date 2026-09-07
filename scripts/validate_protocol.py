"""Standard-library validator for a Patton Protocol package.

Checks the canonical `SKILL.md`, its linked references, and any host
adapters against the structural rules the package must satisfy: valid
frontmatter, resolvable relative links (including README.md's own), the
required lifecycle and orchestration terms, no vendor-required syntax in the
canonical core, a complete worker-report field schema, and well-formed
adapter documents (capability matrix rows and statuses, the human-approval
and nested-worker-spawn boundaries, and rejection of any unnegated claim that
a host-only approval satisfies, replaces, or authorizes an irreversible
action). This module has no third-party dependencies so it runs anywhere
Python 3.11+ runs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LIFECYCLE_TERMS = (
    "scope",
    "plan",
    "dispatch",
    "observe",
    "reconcile",
    "verify",
    "report",
)

REQUIRED_ORCHESTRATION_RULES = (
    "delegation threshold",
    "bounded mission",
    "immutable ownership",
    "recheck",
    "conflict",
    "partial failure",
    "human approval",
    "serial fallback",
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

ADAPTER_REQUIRED_CAPABILITIES = (
    "loading",
    "worker definition",
    "parallel dispatch",
    "serial fallback",
    "approval boundary",
    "nested worker spawn",
)
ADAPTER_ALLOWED_STATUSES = {"native", "prompt-mediated", "unavailable"}

FORBIDDEN_VENDOR_SYNTAX = (
    r"\bclaude\s+--",
    r"\bcodex\s+(?:exec|run)\b",
    r"\bcursor\s+--",
    r"\bspawn_agent\s*\(",
    r"/agents?\b",
)

MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
EXTERNAL_LINK_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

APPROVAL_SUBJECT_PATTERN = re.compile(
    r"\b(?:automated host-only approval(?:s)?|automated approval(?:s)?|"
    r"host-only approval(?:s)?|host permission(?:s)?|workspace permission(?:s)?|"
    r"sandbox(?:es)?)\b"
)
APPROVAL_CANONICAL_BOUNDARY_PATTERN = re.compile(
    r"(?:\bhost-only approval does not satisfy or replace explicit human approval "
    r"and cannot authorize an irreversible action\b\.?|"
    r"\bhost-only approval does not satisfy explicit human approval or authorize "
    r"an irreversible action\b\.?)"
)
APPROVAL_UNSAFE_PREDICATE_PATTERN = re.compile(
    r"\b(?:satisf(?:y|ies|ied|ying|ed)|count(?:s|ed|ing)?\s+as|"
    r"constitut(?:e|es|ed|ing)|(?:is|are|was|were)\s+sufficient|suffices|"
    r"grant(?:s|ed|ing)?|authoriz(?:e|es|ed|ing)|allow(?:s|ed|ing)?|"
    r"permit(?:s|ted|ting)?|approv(?:e|es|ed|ing)|serv(?:e|es|ed|ing)\s+as|"
    r"replac(?:e|es|ed|ing)|enabl(?:e|es|ed|ing)|let(?:s|ting)?)\b"
)
APPROVAL_IRREVERSIBLE_SCOPE_PATTERN = re.compile(r"\birreversible[- ]action(?:s)?\b")
APPROVAL_NEGATION_PATTERN = re.compile(
    r"\b(?:not|never|cannot|can't|do not|does not|fails? to|insufficient to|"
    r"isn't|is not|no)\b"
)
APPROVAL_DOUBLE_NEGATION_PATTERN = re.compile(
    r"\b(?:not|never|cannot|can't|do not|does not|fails? to)\s+"
    r"fail(?:s|ed|ing)?\s+to\b"
)
APPROVAL_LOCAL_BOUNDARY_PATTERN = re.compile(
    r"(?:[,;]|\b(?:and|or|nor|but|however|though|while|yet)\b)"
)
APPROVAL_BARE_COORDINATION_PATTERN = re.compile(r"^\s*,?\s*(?:or|nor)\s*,?\s*$")
APPROVAL_BARE_AND_PATTERN = re.compile(r"^\s*,?\s*and\s*,?\s*$")
APPROVAL_TO_COMPLEMENT_PATTERN = re.compile(r"^\s+to\s+$")
APPROVAL_BARE_PREDICATES = {"replace", "grant", "satisfy"}


def approval_local_prefix(prefix: str) -> str:
    """Keep only the predicate's local condition after the latest boundary."""
    boundaries = list(APPROVAL_LOCAL_BOUNDARY_PATTERN.finditer(prefix))
    if not boundaries:
        return prefix
    return prefix[boundaries[-1].end():]


def find_unsafe_approval_claim(content: str) -> str | None:
    """Find an unsafe approval predicate with no negation in its clause."""
    normalized = " ".join(content.lower().split())
    clauses = re.split(r"(?<=[.!?;])\s+", normalized)
    for clause in clauses:
        subjects = list(APPROVAL_SUBJECT_PATTERN.finditer(clause))
        for index, subject in enumerate(subjects):
            subject_limit = subjects[index + 1].start() if index + 1 < len(subjects) else len(clause)
            tail = clause[subject.end():subject_limit]
            canonical_safe = APPROVAL_CANONICAL_BOUNDARY_PATTERN.fullmatch(
                clause[subject.start():subject_limit].strip()
            ) is not None
            if canonical_safe:
                continue
            if APPROVAL_IRREVERSIBLE_SCOPE_PATTERN.search(tail) is not None:
                return clause
            previous_end = 0
            previous_negated = False
            previous_predicate = ""
            for predicate in APPROVAL_UNSAFE_PREDICATE_PATTERN.finditer(tail):
                raw_prefix = tail[previous_end:predicate.start()]
                local_prefix = approval_local_prefix(raw_prefix)
                if APPROVAL_DOUBLE_NEGATION_PATTERN.search(local_prefix) is not None:
                    return clause
                has_negation = APPROVAL_NEGATION_PATTERN.search(local_prefix) is not None
                coordinated = APPROVAL_BARE_COORDINATION_PATTERN.fullmatch(raw_prefix) is not None
                bare_and = (
                    APPROVAL_BARE_AND_PATTERN.fullmatch(raw_prefix) is not None
                    and predicate.group(0) in APPROVAL_BARE_PREDICATES
                )
                to_complement = (
                    APPROVAL_TO_COMPLEMENT_PATTERN.fullmatch(raw_prefix) is not None
                    and previous_predicate == "suffices"
                )
                continues_negated_phrase = previous_negated and (coordinated or bare_and or to_complement)
                if not has_negation and not continues_negated_phrase:
                    return clause
                previous_negated = has_negation or continues_negated_phrase
                previous_end = predicate.end()
                previous_predicate = predicate.group(0)
    return None


def find_noncanonical_host_only_claim(content: str) -> str | None:
    """Find an irreversible host-only claim outside the constrained grammar."""
    normalized = " ".join(content.lower().split())
    clauses = re.split(r"(?<=[.!?;])\s+", normalized)
    for clause in clauses:
        if APPROVAL_IRREVERSIBLE_SCOPE_PATTERN.search(clause) is None:
            continue
        subjects = list(APPROVAL_SUBJECT_PATTERN.finditer(clause))
        for index, subject in enumerate(subjects):
            subject_limit = subjects[index + 1].start() if index + 1 < len(subjects) else len(clause)
            if APPROVAL_CANONICAL_BOUNDARY_PATTERN.fullmatch(
                clause[subject.start():subject_limit].strip()
            ) is None:
                return clause
    return None


def read_text_or_empty(path: Path) -> str:
    """Return a file's text, or an empty string when the file is absent."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def read_frontmatter(path: Path) -> dict[str, str]:
    """Read the simple scalar YAML frontmatter used by an Agent Skill."""
    text = read_text_or_empty(path)
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


def validate_frontmatter(skill_path: Path) -> list[str]:
    """Require valid, non-empty frontmatter naming the canonical package."""
    frontmatter = read_frontmatter(skill_path)
    if not frontmatter:
        return [f"{skill_path}: missing YAML frontmatter"]
    errors = []
    if frontmatter.get("name") != "patton-protocol":
        errors.append(f"{skill_path}: frontmatter name must equal 'patton-protocol'")
    if not frontmatter.get("description"):
        errors.append(f"{skill_path}: frontmatter description must be non-empty")
    return errors


def find_local_markdown_links(text: str) -> list[str]:
    """Return relative-link targets, skipping external URLs and bare anchors."""
    links = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        target = match.group(1).strip()
        if not target or target.startswith("#") or target.startswith("mailto:"):
            continue
        if EXTERNAL_LINK_PATTERN.match(target):
            continue
        links.append(target)
    return links


def resolve_markdown_links(root: Path, path: Path) -> list[str]:
    """Return one diagnostic per relative link in `path` that fails to resolve."""
    errors = []
    resolved_root = root.resolve()
    for target in find_local_markdown_links(read_text_or_empty(path)):
        link_path = target.split("#", 1)[0]
        if not link_path:
            continue
        resolved = (path.parent / link_path).resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            errors.append(f"{path}: link {target!r} escapes the package root")
            continue
        if not (resolved.is_file() or resolved.is_dir()):
            errors.append(f"{path}: link {target!r} does not resolve to a file or directory")
    return errors


def missing_lifecycle_terms(skill_text: str) -> list[str]:
    """Return every canonical lifecycle term absent from the core skill."""
    lowered = skill_text.lower()
    return [term for term in LIFECYCLE_TERMS if term not in lowered]


def missing_orchestration_rules(skill_text: str) -> list[str]:
    """Return every required orchestration safeguard absent from the core skill."""
    lowered = " ".join(skill_text.lower().split())
    return [rule for rule in REQUIRED_ORCHESTRATION_RULES if rule not in lowered]


def vendor_syntax_violations(skill_text: str) -> list[str]:
    """Return every forbidden vendor-required pattern found in the core skill."""
    lowered = skill_text.lower()
    return [pattern for pattern in FORBIDDEN_VENDOR_SYNTAX if re.search(pattern, lowered)]


def missing_report_fields(worker_report_text: str) -> list[str]:
    """Return every required worker-report field absent from the contract text."""
    lowered = worker_report_text.lower()
    return [field for field in REPORT_FIELDS if field not in lowered]


def validate_report_fields(worker_report_path: Path) -> list[str]:
    """Require every worker-report contract field to appear in the document."""
    if not worker_report_path.is_file():
        return [f"{worker_report_path}: worker report contract is missing"]
    missing = missing_report_fields(read_text_or_empty(worker_report_path))
    if not missing:
        return []
    return [f"{worker_report_path}: missing report fields: {', '.join(missing)}"]


def parse_adapter_capability_rows(path: Path) -> list[tuple[str, list[str]]]:
    """Parse capability/status cells from an adapter's capability matrix."""
    content = read_text_or_empty(path)
    match = re.search(r"^## Capability matrix\s*$", content, re.MULTILINE | re.IGNORECASE)
    if not match:
        return []
    section = content[match.end():]
    next_heading = re.search(r"^##\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]

    rows: list[tuple[str, list[str]]] = []
    for line in section.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        capability = cells[0].lower()
        if capability in ADAPTER_REQUIRED_CAPABILITIES:
            rows.append((capability, re.findall(r"`([^`]+)`", cells[1])))
    return rows


def adapter_capability_errors(path: Path) -> list[str]:
    """Return structural and boundary errors found in an adapter document.

    Covers the full adapter contract, not just the capability matrix: the
    human-approval boundary, the canonical host-only-approval disclaimer, the
    approval-polarity claim (no unnegated claim that a host-only approval
    satisfies, replaces, or authorizes an irreversible action), and the
    nested-worker-spawn boundary, in addition to matrix row/status structure.
    """
    rows = parse_adapter_capability_rows(path)
    by_capability: dict[str, list[list[str]]] = {}
    for capability, statuses in rows:
        by_capability.setdefault(capability, []).append(statuses)

    errors: list[str] = []
    if set(by_capability) != set(ADAPTER_REQUIRED_CAPABILITIES):
        errors.append(f"{path}: matrix must contain every required capability")
    for capability in ADAPTER_REQUIRED_CAPABILITIES:
        statuses = by_capability.get(capability, [])
        if len(statuses) != 1:
            errors.append(f"{path}: {capability} must have one row")
        elif len(statuses[0]) != 1 or statuses[0][0] not in ADAPTER_ALLOWED_STATUSES:
            errors.append(f"{path}: {capability} must have one allowed status")

    content = " ".join(read_text_or_empty(path).lower().split())
    if not re.search(r"human.{0,40}approval|approval.{0,40}human", content):
        errors.append(f"{path}: document must state the human approval boundary")
    if APPROVAL_CANONICAL_BOUNDARY_PATTERN.search(content) is None:
        errors.append(f"{path}: document must state canonical host-only approval boundary")
    if (
        find_unsafe_approval_claim(content) is not None
        or find_noncanonical_host_only_claim(content) is not None
    ):
        errors.append(f"{path}: approval boundary must reject host-only approval")
    if not re.search(r"nested.{0,120}(worker|agent|subagent).{0,120}spawn", content):
        errors.append(f"{path}: document must state the nested worker-spawn boundary")
    return errors


def validate_package(root: Path) -> list[str]:
    """Run every structural check against a Patton Protocol package root."""
    root = root.resolve()
    skill_path = root / "SKILL.md"
    errors: list[str] = []

    if not skill_path.is_file():
        return [f"{skill_path}: canonical SKILL.md is missing"]

    errors.extend(validate_frontmatter(skill_path))

    skill_text = read_text_or_empty(skill_path)
    missing_terms = missing_lifecycle_terms(skill_text)
    if missing_terms:
        errors.append(f"{skill_path}: missing lifecycle terms: {', '.join(missing_terms)}")

    missing_rules = missing_orchestration_rules(skill_text)
    if missing_rules:
        errors.append(f"{skill_path}: missing orchestration rules: {', '.join(missing_rules)}")

    errors.extend(f"{skill_path}: canonical core requires vendor-specific syntax matching {pattern!r}"
                  for pattern in vendor_syntax_violations(skill_text))

    markdown_files = [skill_path]
    readme_path = root / "README.md"
    if readme_path.is_file():
        markdown_files.append(readme_path)
    references_dir = root / "references"
    if references_dir.is_dir():
        markdown_files.extend(sorted(references_dir.glob("*.md")))
    adapters_dir = root / "adapters"
    if adapters_dir.is_dir():
        markdown_files.extend(sorted(adapters_dir.glob("*.md")))

    for path in markdown_files:
        errors.extend(resolve_markdown_links(root, path))

    worker_report_path = references_dir / "worker-report.md"
    if worker_report_path.is_file():
        errors.extend(validate_report_fields(worker_report_path))

    if adapters_dir.is_dir():
        for adapter_path in sorted(adapters_dir.glob("*.md")):
            errors.extend(adapter_capability_errors(adapter_path))

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: validate_protocol.py <package-root>", file=sys.stderr)
        return 2
    root = Path(argv[0])
    if not root.is_dir():
        print(f"{root}: not a directory", file=sys.stderr)
        return 2

    errors = validate_package(root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"invalid: {len(errors)} error(s)", file=sys.stderr)
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
