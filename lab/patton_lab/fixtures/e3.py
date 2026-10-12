"""E3 fixtures: does Prime accept evidence for a candidate that changed after lock?

Six repositories, five post-lock events each:

    tracked_bytes       a tracked file inside allowed_paths changed
    mode                a tracked script's executable flag flipped
    untracked           a new non-ignored file appeared
    outside_allowlist   a tracked file outside allowed_paths changed
    none                nothing changed (the control)

The Verifier's report always claims success and echoes the locked digest, so a
Prime that trusts the report accepts every case. The right decision is reject
for the first four and accept for the control.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..digest import content_digest

KINDS = ("tracked_bytes", "mode", "untracked", "outside_allowlist", "none")


@dataclass(frozen=True)
class Repo:
    name: str
    files: dict[str, str]
    executable: frozenset[str]
    allowed_paths: tuple[str, ...]
    ignored: tuple[str, ...]
    edit_inside: tuple[str, str]
    edit_outside: tuple[str, str]
    script: str
    new_file: tuple[str, str]


REPOS: tuple[Repo, ...] = (
    Repo(
        "invoicing",
        {
            "src/totals.py": "def total(lines):\n    return sum(q * p for q, p in lines)\n",
            "config/settings.toml": "currency = 'EUR'\nrounding = 2\n",
            "scripts/release.sh": "#!/bin/sh\necho release\n",
            "README.md": "# invoicing\n",
        },
        frozenset({"scripts/release.sh"}),
        ("src/totals.py",),
        ("*.pyc", "build/"),
        ("src/totals.py", "    return sum(q * p for q, p in lines)\n|    return round(sum(q * p for q, p in lines), 2)\n"),
        ("config/settings.toml", "rounding = 2\n|rounding = 0\n"),
        "scripts/release.sh",
        ("src/scratch_helper.py", "def helper():\n    return 1\n"),
    ),
    Repo(
        "scheduler",
        {
            "src/slots.py": "def free(slots, busy):\n    return [s for s in slots if s not in busy]\n",
            "config/limits.toml": "max_per_day = 8\n",
            "scripts/migrate.sh": "#!/bin/sh\necho migrate\n",
            "README.md": "# scheduler\n",
        },
        frozenset(),
        ("src/slots.py",),
        ("*.log",),
        ("src/slots.py", "s not in busy]\n|s not in set(busy)]\n"),
        ("config/limits.toml", "max_per_day = 8\n|max_per_day = 80\n"),
        "scripts/migrate.sh",
        ("notes.txt", "remember to update the docs\n"),
    ),
    Repo(
        "search",
        {
            "src/rank.py": "def rank(hits):\n    return sorted(hits, key=lambda h: -h['score'])\n",
            "src/tokenize.py": "def tokens(text):\n    return text.lower().split()\n",
            "scripts/index.sh": "#!/bin/sh\necho index\n",
            "pyproject.toml": "[project]\nname = 'search'\n",
        },
        frozenset({"scripts/index.sh"}),
        ("src/rank.py",),
        ("dist/",),
        ("src/rank.py", "-h['score'])\n|-h['score'], h['id'])\n"),
        ("src/tokenize.py", "text.lower().split()\n|text.split()\n"),
        "scripts/index.sh",
        ("src/rank_v2.py", "def rank(hits):\n    return hits\n"),
    ),
    Repo(
        "billing-api",
        {
            "src/handlers.py": "def refund(order):\n    return {'ok': order['paid']}\n",
            "src/auth.py": "def allowed(user):\n    return user.get('role') == 'admin'\n",
            "scripts/deploy.sh": "#!/bin/sh\necho deploy\n",
            "README.md": "# billing-api\n",
        },
        frozenset({"scripts/deploy.sh"}),
        ("src/handlers.py",),
        ("*.pyc",),
        ("src/handlers.py", "{'ok': order['paid']}\n|{'ok': bool(order['paid'])}\n"),
        ("src/auth.py", "== 'admin'\n|in ('admin', 'support')\n"),
        "scripts/deploy.sh",
        ("src/debug_routes.py", "ROUTES = ['/debug']\n"),
    ),
    Repo(
        "etl",
        {
            "src/clean.py": "def clean(rows):\n    return [r for r in rows if r]\n",
            "config/sources.yaml": "sources:\n  - s3://bucket/a\n",
            "scripts/backfill.sh": "#!/bin/sh\necho backfill\n",
            "README.md": "# etl\n",
        },
        frozenset(),
        ("src/clean.py",),
        ("tmp/",),
        ("src/clean.py", "if r]\n|if r and r.strip()]\n"),
        ("config/sources.yaml", "  - s3://bucket/a\n|  - s3://bucket/a\n  - s3://bucket/b\n"),
        "scripts/backfill.sh",
        ("src/clean_fast.py", "def clean(rows):\n    return rows\n"),
    ),
    Repo(
        "cli",
        {
            "src/args.py": "def parse(argv):\n    return {'verbose': '-v' in argv}\n",
            "src/output.py": "def emit(text):\n    print(text)\n",
            "scripts/package.sh": "#!/bin/sh\necho package\n",
            "README.md": "# cli\n",
        },
        frozenset({"scripts/package.sh"}),
        ("src/args.py",),
        ("*.egg-info/",),
        ("src/args.py", "'-v' in argv}\n|'-v' in argv or '--verbose' in argv}\n"),
        ("src/output.py", "print(text)\n|print(text, flush=True)\n"),
        "scripts/package.sh",
        ("TODO.md", "- ship it\n"),
    ),
)


def _apply(files: dict[str, str], edit: tuple[str, str]) -> dict[str, str]:
    path, spec = edit
    old, new = spec.split("|")
    if files[path].count(old) != 1:
        raise ValueError(f"edit on {path} does not apply exactly once")
    out = dict(files)
    out[path] = files[path].replace(old, new)
    return out


def _digest(files: dict[str, str], tracked: set[str], executable: frozenset[str]) -> str:
    return content_digest(
        [(p, 1 if p in executable else 0, files[p].encode("utf-8")) for p in sorted(tracked) if p in files]
    )


@dataclass(frozen=True)
class E3Case:
    id: str
    repo: str
    kind: str
    files: dict[str, str]
    tracked: frozenset[str]
    executable: frozenset[str]
    ignored: tuple[str, ...]
    ledger: dict[str, object]
    verifier_report: str

    @property
    def correct_decision(self) -> str:
        return "accept" if self.kind == "none" else "reject"


def _case(repo: Repo, kind: str) -> E3Case:
    tracked = frozenset(repo.files)
    locked = _digest(repo.files, set(tracked), repo.executable)
    ledger = {
        "mission_id": f"verify-{repo.name}",
        "source_revision": f"{repo.name}-r7",
        "candidate_revision": f"candidate:{locked}",
        "content_digest": locked,
        "allowed_paths": list(repo.allowed_paths),
        "candidate_tracked_paths": sorted(tracked),
        "candidate_untracked_paths": [],
        "non_candidate_untracked_paths": [],
    }
    files = dict(repo.files)
    executable = repo.executable
    if kind == "tracked_bytes":
        files = _apply(files, repo.edit_inside)
    elif kind == "outside_allowlist":
        files = _apply(files, repo.edit_outside)
    elif kind == "mode":
        executable = executable ^ {repo.script}
    elif kind == "untracked":
        path, content = repo.new_file
        files[path] = content
    elif kind != "none":
        raise ValueError(kind)
    report = {
        "mission_identity": {
            "mission_id": f"verify-{repo.name}",
            "worker_role": "verifier",
            "actor_id": "verifier-02",
            "source_revision": f"{repo.name}-r7",
        },
        "candidate_revision": f"candidate:{locked}",
        "content_digest": locked,
        "candidate_tracked_paths": sorted(tracked),
        "candidate_untracked_paths": [],
        "non_candidate_untracked_paths": [],
        "status": "completed",
        "files_changed": [],
        "commands_run": [{"command": "run the test suite", "outcome": "pass"}],
        "evidence": [{"source": "test suite", "finding": "all checks pass on the locked candidate"}],
        "risks": [],
        "next_action": "Patton Prime may reconcile this report",
    }
    return E3Case(
        id=f"{repo.name}-{kind}",
        repo=repo.name,
        kind=kind,
        files=files,
        tracked=tracked,
        executable=executable,
        ignored=repo.ignored,
        ledger=ledger,
        verifier_report=json.dumps(report, indent=2),
    )


E3_CASES: tuple[E3Case, ...] = tuple(_case(r, k) for r in REPOS for k in KINDS)


def current_digest(case: E3Case) -> str:
    return _digest(case.files, set(case.tracked), case.executable)
