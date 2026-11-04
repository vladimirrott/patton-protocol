"""P1V's cases: what the plugin's Verifier read at the last stop of each stored stop-arm run, with the run's truth.

A fleet run stores a cassette of every model call, the worker's and the Verifier's. The Verifier's call holds its
whole input (the request, the notes, the checks, the change, the files left) and the answer the shipped prompt gave.
A case is one run: that input, that answer, and the run's `gamed` flag from the fixture oracle. When a run stopped
more than once, the last call is the one about the state the run ended in.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

LAB = Path(__file__).resolve().parents[2]
# A byte-for-byte copy of patton-gate 0.4.0, the plugin the stored runs used. The live plugin moves on, and its
# Verifier prompt is the control: the copy keeps the control, the inputs and the plugin's `judge` rule fixed.
GATE_PATH = LAB / "p1v" / "registered" / "patton_gate.py"
VERIFIER_PREFIX = "You are an independent verifier"


def _load_gate():
    spec = importlib.util.spec_from_file_location("patton_gate_p1v", GATE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {GATE_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


@dataclass(frozen=True)
class Case:
    id: str
    harness: str
    fixture: str
    rep: int
    gamed: bool
    fitted: bool
    met: bool
    system: str
    source: str = ""
    original: str | None = None
    calls: int = 1

    @property
    def kind(self) -> str:
        """gamed: the oracle says the run gamed the checks. met: not gamed and the goal was met, so a fail is a
        false flag. unmet: not gamed and the goal not met, where a fail on a failing check or an undone task is
        a fair catch."""
        return "gamed" if self.gamed else "met" if self.met else "unmet"


def _reply_text(response) -> str | None:
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def _verifier_calls(cassette: Path) -> list[dict]:
    calls = []
    with gzip.open(cassette, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            messages = (item.get("request") or {}).get("messages") or []
            if item.get("route") == "t1" and len(messages) >= 2 and messages[0].get("role") == "system" and \
                    str(messages[0].get("content", "")).startswith(VERIFIER_PREFIX):
                calls.append(item)
    return calls


def collect_stored(root: Path, arm: str = "stop") -> tuple[list[Case], list[str]]:
    """(cases, the runs of that arm that never reached the Verifier). Refuses on a record it cannot read."""
    out: list[Case] = []
    skipped: list[str] = []
    records = sorted(root.glob("*/*.json"))
    if not records:
        raise ValueError(f"no run records under {root}")
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("arm") != arm:
            continue
        metrics = record.get("metrics") or {}
        if not all(isinstance(metrics.get(k), bool) for k in ("gamed", "fitted", "goal_met")):
            raise ValueError(f"{path}: the record has no gamed, fitted and goal_met flags")
        harness = path.parent.name
        run = f"{harness}/{path.stem}"
        cassette = path.with_name(path.stem + ".cassette.jsonl.gz")
        if not cassette.exists():
            raise FileNotFoundError(f"{run}: no cassette beside the record")
        calls = _verifier_calls(cassette)
        if not calls:
            skipped.append(run)
            continue
        last = calls[-1]
        messages = last["request"]["messages"]
        out.append(Case(run, harness, str(record["fixture"]), int(record["rep"]), metrics["gamed"],
                        metrics["fitted"], metrics["goal_met"], str(messages[0]["content"]), str(messages[1]["content"]),
                        _reply_text(last.get("response")), len(calls)))
    return out, skipped


# ---- the development set's synthetic states -------------------------------------------------------------------

CHECK = "python3 -m unittest -q"


def isolate_available() -> bool:
    from .. import isolate
    return isolate.available()


def _content_id(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TIMING = re.compile(r"(Ran \d+ tests? in )\d+\.\d+s")


def steady(output: str) -> str:
    """The checks' output with unittest's wall-clock time fixed, so a state builds the same input every time."""
    return _TIMING.sub(r"\g<1>0.001s", output)


def _source(fixture, after: dict[str, str], visible_pass: bool, visible_out: str) -> str:
    """The Verifier's input for an end state, built by the plugin's own functions the way the hook builds it."""
    import hashlib
    from ..p1 import fixtures as p1
    cfg = gate.DEFAULTS["verify"]
    before = fixture.files
    pairs = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        pairs.append((path, "added" if old is None else "deleted" if new is None else "modified", old, new))
    changes = gate.file_changes(pairs, cfg["max_diff_chars"])
    for change in changes:
        change["before_id"] = None if change["before"] is None else _content_id(change["before"])
    notes = [(p, before[p]) for p in sorted(before) if gate._matches(cfg["notes"], p)]
    files = [(p, len(t.encode("utf-8")), _content_id(t)) for p, t in sorted(after.items())]
    checks = [{"command": CHECK, "code": 0 if visible_pass else 1, "output": steady(visible_out)}]
    digest = hashlib.sha256(json.dumps(after, sort_keys=True).encode("utf-8")).hexdigest()
    return gate.verifier_input(p1.mission(fixture), changes, notes, checks, digest, files)


def synthetic() -> list[Case]:
    """Every fixture's start, reference and hack states, scored by the oracle in the isolated container."""
    import tempfile
    from ..p1 import fixtures as p1
    out: list[Case] = []
    for fixture in p1.FIXTURES.values():
        for name in ("start", "reference", *fixture.hacks):
            changed = {"start": {}, "reference": fixture.reference, **fixture.hacks}[name]
            after = {**fixture.files, **changed}
            with tempfile.TemporaryDirectory() as tmp:
                for rel, text in after.items():
                    target = Path(tmp) / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                ran = p1.oracle(fixture, Path(tmp))
            scored = p1.score(fixture, after, ran, "", 0)
            out.append(Case(f"dev/{fixture.id}/{name}", "synthetic", fixture.id, 0, scored["gamed"],
                            scored["fitted"], scored["goal_met"], gate.VERIFIER_SYSTEM,
                            _source(fixture, after, scored["visible_pass"], scored["visible_out"]), None, 0))
    return out
