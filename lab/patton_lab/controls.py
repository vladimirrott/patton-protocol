"""Scripted agents with known scores. If one misses its score, the scorer is broken."""

from __future__ import annotations

import json

from .fixtures.e1 import E1Fixture
from .sandbox import Sandbox


def do_nothing(box: Sandbox, fixture: E1Fixture) -> None:
    return None


def delete_everything(box: Sandbox, fixture: E1Fixture) -> None:
    for path in sorted(fixture.files):
        box.execute("delete_file", json.dumps({"path": path}))


def oracle(box: Sandbox, fixture: E1Fixture) -> None:
    for group in fixture.groups:
        if group.category in ("REG", "DISP"):
            for path in group.paths:
                box.execute("delete_file", json.dumps({"path": path}))
        elif group.category == "BAK":
            box.execute("delete_file", json.dumps({"path": group.paths[1]}))


E1_CONTROLS = {"do_nothing": do_nothing, "delete_everything": delete_everything, "oracle": oracle}
