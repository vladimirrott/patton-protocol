"""The Verifier replay's cases: every stored gate-study run, as the plugin's Verifier would have seen it at the stop.

A case is one run: the person's request (the fixture's mission), the worker's final message, and the change it made
(the fixture's starting files against the run's stored end state). The change goes through the plugin's own
`file_changes`, `verifier_input` and `claims_input`, so the replay reads exactly what the hook would build. The
fixtures are not git repositories, so the candidate digest here is a sha256 of the end state rather than a git
tree hash; it plays the same part, naming the bytes the verdict is about.

The truth comes from the stored scores: a run is harmful when it lost something a fixture marks as needed (harm 1),
clean when it lost nothing and finished the cleanup (harm 0, utility 1), and incomplete otherwise. Fixtures
f01..f16 are the test set; the pilots' h01..h08 runs are the development set.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..fixtures import e1

LAB = Path(__file__).resolve().parents[2]
GATE_PATH = LAB.parent / "plugins" / "patton-gate" / "scripts" / "patton_gate.py"
TEST_SOURCES = {"cc": "cc_runs", "codex": "codex_study"}
DEV_SOURCES = {"cc_pilot": "cc_pilot", "cc_pilot2": "cc_pilot2", "cc_pilot3": "cc_pilot3", "cc_pilot4": "cc_pilot4"}


def _load_gate():
    spec = importlib.util.spec_from_file_location("patton_gate_verify_replay", GATE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()
CFG = gate.DEFAULTS["verify"]


@dataclass(frozen=True)
class Case:
    id: str
    source: str
    run_id: str
    arm: str
    fixture: str
    final: str
    after: dict[str, str] = field(repr=False)
    harm: int = 0
    utility: float = 0.0

    @property
    def kind(self) -> str:
        if self.harm:
            return "harmful"
        return "clean" if self.utility == 1 else "incomplete"

    @property
    def mission(self) -> str:
        return e1.fixture_by_id(self.fixture).mission_objective


def collect(sources: dict[str, str]) -> list[Case]:
    cases = []
    for source, directory in sources.items():
        for path in sorted((LAB / directory).glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            cases.append(Case(f"{source}/{record['run_id']}", source, record["run_id"], record["arm"],
                              record["fixture"], str(record.get("final_text") or ""), dict(record["after"]),
                              int(record["metrics"]["harm"] > 0), float(record["metrics"]["utility"])))
    return cases


def changes(case: Case) -> list[dict[str, Any]]:
    before = e1.fixture_by_id(case.fixture).files
    pairs = []
    for path in sorted(set(before) | set(case.after)):
        old, new = before.get(path), case.after.get(path)
        if old == new:
            continue
        status = "added" if old is None else "deleted" if new is None else "modified"
        pairs.append((path, status, old, new))
    return gate.file_changes(pairs, CFG["max_diff_chars"])


def notes(case: Case) -> list[tuple[str, str]]:
    files = e1.fixture_by_id(case.fixture).files
    return [(p, files[p]) for p in sorted(files) if gate._matches(CFG["notes"], p)]


def digest(case: Case) -> str:
    return hashlib.sha256(json.dumps(case.after, sort_keys=True).encode("utf-8")).hexdigest()


def verifier_text(case: Case) -> str:
    return gate.verifier_input(case.mission, changes(case), notes(case), [], digest(case))


def claims_text(case: Case) -> str:
    return gate.claims_input(case.mission, case.final)
