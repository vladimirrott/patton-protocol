"""The diff gate (R2): the worker edits a scratch copy and an approver decides which changes to existing files land.

The worker never meets a gate, so a diff-gate run is an unguarded run seen through a commit step. R2 has no
live runs of its own: `evaluate` replays a recorded unguarded run under any approver.
"""

from __future__ import annotations

from typing import Any, Callable

from ..score import score_e1
from .approvers import make_approver

Approver = Callable[[list[str]], tuple[list[str], list[str]]]


def proposals(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Paths that existed before and are missing or different after, sorted. New files are never proposals."""
    return sorted(path for path, text in before.items() if after.get(path) != text)


def commit(before: dict[str, str], after: dict[str, str], approver: Approver) -> dict[str, str]:
    """The committed tree: approved changes stand, refused ones revert to the original bytes, new files land."""
    committed = dict(after)
    for path in proposals(before, after):
        approved, _denied = approver([path])
        if path not in approved:
            committed[path] = before[path]
    return committed


def evaluate(record: dict[str, Any], fixture: Any, approver_name: str) -> dict[str, Any]:
    """Score a recorded unguarded run as if its changes had passed through the diff gate."""
    before = dict(fixture.files)
    run_id = f"{record.get('arm')}-{record.get('fixture')}-r{record.get('rep', 0)}"
    approver = make_approver(approver_name, fixture, run_id)
    asked: list[str] = []

    def spy(paths: list[str]) -> tuple[list[str], list[str]]:
        asked.extend(paths)
        return approver(paths)

    committed = commit(before, record["after"], spy)
    metrics = score_e1(fixture, before, committed, [{"tool": name} for name in record["tool_names"]],
                       [{"paths": [path]} for path in asked], record["final_text"])
    metrics.update(approvals=len(asked), approver=approver_name)
    return metrics
