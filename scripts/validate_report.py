#!/usr/bin/env python3
"""Check that a worker report carries the fields references/worker-report.md requires.

    python3 scripts/validate_report.py REPORT_FILE

The report may be JSON or the YAML-shaped envelope, alone or inside prose (a
fenced block or a bare object). This checks field presence and the status
vocabulary. It does not judge whether the evidence is true; that is the
Verifier's job.

Exit 0 and print `valid` when every field is present; exit 1 and print one
problem per line otherwise. Standard library only, like the rest of scripts/.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TOP_FIELDS = ("mission_identity", "status", "files_changed", "commands_run", "evidence", "risks", "next_action")
IDENTITY_FIELDS = ("mission_id", "worker_role", "actor_id", "source_revision")
STATUSES = ("completed", "partial", "blocked", "failed")

_KEY_LINE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*)$")


def _json_report(text: str, strict: bool) -> dict | None:
    """The first object that looks like a report; with strict off, else the first with two or more keys."""
    decoder = json.JSONDecoder()
    fallback = None
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if "status" in obj or "mission_identity" in obj:
            return obj
        if not strict and fallback is None and len(obj) >= 2:
            fallback = obj
    return fallback


def _yaml_report(text: str) -> dict | None:
    """Read top-level keys and the mission_identity block from YAML-shaped text."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        match = _KEY_LINE.match(line)
        if match and match.group("key") in TOP_FIELDS:
            start = index
            break
    if start is None:
        return None
    base_indent = len(_KEY_LINE.match(lines[start]).group("indent"))
    report: dict = {}
    current_top = None
    for line in lines[start:]:
        if line.strip().startswith("```"):
            break
        match = _KEY_LINE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        key = match.group("key")
        value = match.group("value").strip().strip('"').strip("'")
        if indent == base_indent:
            current_top = key
            report[key] = {} if key == "mission_identity" else value
        elif indent > base_indent and current_top == "mission_identity":
            report["mission_identity"][key] = value
    return report


def extract_report(text: str) -> dict | None:
    return _json_report(text, strict=True) or _yaml_report(text) or _json_report(text, strict=False)


def report_problems(text: str) -> list[str]:
    report = extract_report(text)
    if report is None:
        return ["no report found: no JSON object or YAML block carries a status or mission_identity"]
    problems = []
    for field in TOP_FIELDS:
        if field not in report:
            problems.append(f"missing field: {field}")
    identity = report.get("mission_identity")
    if isinstance(identity, dict):
        for field in IDENTITY_FIELDS:
            if field not in identity:
                problems.append(f"missing field: mission_identity.{field}")
    elif "mission_identity" in report:
        problems.append("mission_identity is not a mapping")
    status = report.get("status")
    if "status" in report and str(status).strip().lower() not in STATUSES:
        problems.append(f"status {status!r} is not one of {', '.join(STATUSES)}")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: validate_report.py REPORT_FILE", file=sys.stderr)
        return 2
    text = sys.stdin.read() if argv[0] == "-" else Path(argv[0]).read_text(encoding="utf-8")
    problems = report_problems(text)
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
