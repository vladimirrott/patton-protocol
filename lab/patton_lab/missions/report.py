"""The subagent-limit pilot's tables, generated from its records, and the check that the record quotes them.

    python3 -m patton_lab.missions.report            # print both tables
    python3 -m patton_lab.missions.report --check    # fail if docs/records quotes anything else

The second table is post hoc: it replays each M-OFF run's dispatches, in order, through the `patton` preset's
dispatch rules (max_retries 2 on the same description and type, max_dispatches 12 in all) and counts what the
gate would have refused. It leaves out max_workers, which depends on when each subagent ran.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import fixtures as fx
from . import runner

RECORD = runner.REPO / "docs" / "records" / "2026-10-23-missions-pilot.md"
MARKS = {"facts": ("<!-- facts -->", "<!-- /facts -->"), "summary": ("<!-- summary -->", "<!-- /summary -->"),
         "replay": ("<!-- replay -->", "<!-- /replay -->")}
RETRIES, TOTAL = 2, 12


def load(out: Path = runner.OUT) -> list[dict]:
    rows = [json.loads(p.read_text()) for p in sorted(out.glob("*.json"))]
    if len(rows) != len(runner.plan()):
        raise ValueError(f"expected {len(runner.plan())} pilot runs, found {len(rows)}")
    return rows


def would_refuse(dispatches: list[tuple[str, str]]) -> tuple[int, int]:
    """(refused for retries, refused for the total) when the preset sees these dispatches in this order."""
    seen: dict[tuple[str, str], int] = {}
    total = by_retries = by_total = 0
    for key in dispatches:  # the gate's order: the retry limit first, then the total
        if seen.get(key, 0) > RETRIES:
            by_retries += 1
            continue
        if total >= TOTAL:
            by_total += 1
            continue
        seen[key] = seen.get(key, 0) + 1
        total += 1
    return by_retries, by_total


def replay_table(rows: list[dict]) -> str:
    lines = ["| M-OFF run | dispatches | refused for max_retries | refused for max_dispatches |",
             "| --- | --- | --- | --- |"]
    for r in rows:
        if r["arm"] != "M-OFF":
            continue
        keys = [(str(t["input"].get("subagent_type", "")), str(t["input"].get("description", "")))
                for t in r["tools"] if t["name"] in ("Agent", "Task")]
        a, b = would_refuse(keys)
        lines.append(f"| {r['run_id']} | {len(keys)} | {a} | {b} |")
    return "\n".join(lines) + "\n"


def facts(rows: list[dict]) -> str:
    versions = sorted({r["claude_version"] for r in rows})
    calls = sum(len(r["calls"]) for r in rows)
    spent = sum(r["usd"] for r in rows)
    charged = runner._ledger_sum(runner.LEDGER)
    return (f"- {len(rows)} runs, Claude Code {', '.join(versions)}, model {runner.GPT_OSS} through Groq, "
            f"{calls} model calls, all recorded.\n"
            f"- Spend: ${spent:.4f} on the stored runs; the pilot's ledger holds ${charged:.4f}.\n")


def tables(out: Path = runner.OUT) -> dict[str, str]:
    rows = load(out)
    return {"facts": facts(rows), "summary": runner.summary(out), "replay": replay_table(rows)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.missions.report")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    made = tables()
    if not args.check:
        for text in made.values():
            sys.stdout.write(text + "\n")
        return 0
    doc = RECORD.read_text(encoding="utf-8")
    for name, (begin, end) in MARKS.items():
        quoted = doc.split(begin, 1)[1].split(end, 1)[0].strip("\n") + "\n" if begin in doc else ""
        if quoted != made[name]:
            print(f"{RECORD.name} quotes a different {name} table; regenerate it")
            return 1
    print("the record quotes the tables the pilot's records give")
    return 0


if __name__ == "__main__":
    sys.exit(main())
