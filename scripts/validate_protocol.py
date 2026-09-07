"""Standard-library validator for a Patton Protocol package.

Checks the canonical `SKILL.md`, its linked references, and any host
adapters against the structural rules the package must satisfy: valid
frontmatter, resolvable relative links, the required lifecycle and
orchestration terms, no vendor-required syntax in the canonical core, a
complete worker-report field schema, and well-formed adapter capability
matrices. This module has no third-party dependencies so it runs anywhere
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
        if not resolved.exists():
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
    """Return structural errors in an adapter's capability matrix."""
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
