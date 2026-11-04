"""Replay Verifier prompts over stored stop-arm inputs on Groq, recording every call.

    python3 -m patton_lab.p1v.runner plan                          # calls and the spend bound
    python3 -m patton_lab.p1v.runner trace                         # $0: a scripted upstream through every path
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.p1v.runner dev     # paid: the development set
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.p1v.runner study   # paid: the registered test set
    python3 -m patton_lab.p1v.runner replay --set dev|study        # re-decide every stored answer offline

Each case and prompt costs one call, made the way the plugin makes the Verifier's call (its input, max_tokens 4096,
no reasoning setting). The plugin's own `judge` turns the reply into a decision, so a fail without evidence copied
from the input allows. Money and stops follow the verify runner: every response that reports usage is charged to
lab/p1v_spend.jsonl, each call's worst case is held under the set's cap and under the lab-wide cap over every
lab/*_spend.jsonl, a 429 waits, a 5xx retries twice and stops the run, and the same command resumes it.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import spend_all
from ..triage import runner as triage
from ..verify import runner as verify
from . import cases as cases_mod
from . import prompts as prompts_mod

LAB = cases_mod.LAB
LEDGER = LAB / "p1v_spend.jsonl"
TRACE_DIR = LAB / "p1v_trace"
PROXY = triage.PROXY
MODEL = "qwen3.8-27b"
SETS = {"dev": 1.00, "study": 2.00}  # cap on this ledger while the set runs; the dev runs count toward the study's
PER_MODEL = 4
DEV_DRAWS = 3  # the development runs draw each prompt this many times
DEV_ARMS = prompts_mod.replicated(prompts_mod.ARMS, DEV_DRAWS)
gate = cases_mod.gate


class SpendStop(Exception):
    pass


class Meter(triage.Meter):
    """The triage meter, counting every lab/*_spend.jsonl (spend_all), this study's included."""

    def reserve(self, amount: float) -> None:
        with self.lock:
            spent = spend_all.spent_everywhere(self.lab)
            here = spend_all.ledger_sum(self.ledger) if self.ledger.exists() else 0.0
            if here + self.held + amount > self.cap:
                raise SpendStop(f"the P1V ledger holds ${here:.4f}; another call could pass this study's ${self.cap} cap")
            if sum(spent.values()) + self.held + amount > spend_all.GLOBAL_CAP:
                raise SpendStop(f"the lab has spent ${sum(spent.values()):.4f}; another call could pass the "
                                f"${spend_all.GLOBAL_CAP} cap")
            self.held += amount


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def decide(reply: str | None, source: str) -> dict[str, Any]:
    """The plugin's rule on one reply: the Verifier's verdict, what it found, and whether the stop would be refused."""
    verdict, findings, judged = gate.judge(reply, source)
    return {"verdict": verdict, "findings": findings, "judged": judged, "flagged": judged.startswith("block")}


def ask(url: str, case: cases_mod.Case, name: str, system: str, meter: Meter, stop: threading.Event,
        sleep=time.sleep, tag: str = "") -> dict[str, Any]:
    msgs = verify.messages(system, case.source)
    reply = verify.call(url, MODEL, msgs, meter, stop, f"{tag}{name}/{case.id}", sleep)
    content = reply["content"] if reply["status"] == 200 else None
    return {"case": case.id, "prompt": name, "kind": case.kind, "fixture": case.fixture, "harness": case.harness,
            "input_sha": sha(msgs), "reply": reply, **decide(content, case.source), "usd": reply["usd"]}


def store_path(out: Path, name: str) -> Path:
    return out / f"{name}.jsonl.gz"


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_set(dataset: list[cases_mod.Case], out: Path, url: str, meter: Meter, prompts: dict[str, str] | None = None,
            per_model: int = PER_MODEL, sleep=time.sleep) -> int:
    prompts = prompts or prompts_mod.ARMS
    out.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    lock = threading.Lock()
    gates = threading.Semaphore(per_model)
    problems: list[str] = []
    jobs = []
    for name in prompts:
        done = {r["case"] for r in load(store_path(out, name))}
        jobs += [(name, c) for c in dataset if c.id not in done]

    def one(job) -> None:
        name, case = job
        if stop.is_set():
            return
        with gates:
            try:
                record = ask(url, case, name, prompts[name], meter, stop, sleep, f"{out.name}/")
            except (SpendStop, triage.SpendStop, triage.UpstreamStop) as err:
                stop.set()
                problems.append(str(err))
                return
        with lock:
            with gzip.open(store_path(out, name), "at", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    with ThreadPoolExecutor(max_workers=per_model) as pool:
        list(pool.map(one, jobs))
    for problem in sorted(set(problems))[:5]:
        print(f"!! {problem}")
    return 2 if problems else 0


def replay(dataset: list[cases_mod.Case], out: Path, prompts: dict[str, str] | None = None) -> list[str]:
    prompts = prompts or prompts_mod.ARMS
    by_id = {c.id: c for c in dataset}
    problems = []
    for name, system in prompts.items():
        records = load(store_path(out, name))
        seen = [r["case"] for r in records]
        if len(seen) != len(set(seen)):
            problems.append(f"{name}: a case is stored twice")
        if set(seen) != set(by_id):
            problems.append(f"{name}: {len(set(by_id) - set(seen))} case(s) missing, "
                            f"{len(set(seen) - set(by_id))} unknown")
        for record in records:
            case = by_id.get(record["case"])
            if case is None:
                continue
            if record["input_sha"] != sha(verify.messages(system, case.source)):
                problems.append(f"{name}/{case.id}: the inputs changed since the call")
                continue
            reply = record["reply"]
            again = decide(reply["content"] if reply["status"] == 200 else None, case.source)
            if any(again[k] != record[k] for k in again):
                problems.append(f"{name}/{case.id}: the stored decision differs from the rules")
            if record["kind"] != case.kind:
                problems.append(f"{name}/{case.id}: the truth changed since the call")
    return problems


TRACED = ("cases.py", "prompts.py", "runner.py")


def stamp() -> str:
    here = Path(__file__).resolve().parent
    parts = [(here / name).read_bytes() for name in TRACED] + [
        cases_mod.GATE_PATH.read_bytes(), triage.CONFIG.read_bytes(),
        (here.parent / "spend_all.py").read_bytes()]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def datasets(which: str) -> list[cases_mod.Case]:
    if which == "study":
        got, _ = cases_mod.collect_stored(LAB / "fleet_p1r")
        return got
    pilot, _ = cases_mod.collect_stored(LAB / "fleet_p1")
    return pilot + cases_mod.synthetic()


def plan_lines(dataset: list[cases_mod.Case], cap: float, prompts: dict[str, str] | None = None) -> list[str]:
    prompts = prompts or prompts_mod.ARMS
    lines, total = [], 0.0
    for name, system in prompts.items():
        worst = sum(triage.bound(MODEL, verify.messages(system, c.source)) for c in dataset)
        total += worst
        lines.append(f"{name:12} {len(dataset):4} calls  worst case ${worst:.4f}")
    lines.append(f"{'all':12} {len(dataset) * len(prompts):4} calls  worst case ${total:.4f}  (cap ${cap:.2f})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.p1v.runner")
    parser.add_argument("command", choices=("plan", "trace", "dev", "study", "replay"))
    parser.add_argument("--set", default="study", choices=tuple(SETS))
    args = parser.parse_args(argv)
    if args.command == "trace":
        from . import trace
        return trace.main(TRACE_DIR, stamp())
    which = args.set if args.command in ("plan", "replay") else args.command
    dataset = datasets(which)
    out = LAB / f"p1v_{which}"
    if args.command == "plan":
        print("\n".join(plan_lines(dataset, SETS[which], DEV_ARMS if which == "dev" else prompts_mod.STUDY_ARMS)))
        return 0
    if args.command == "replay":
        problems = replay(dataset, out, DEV_ARMS if which == "dev" else prompts_mod.STUDY_ARMS)
        print("\n".join(problems) if problems else f"replay OK: every stored answer in {out.name} re-decides the same")
        return 1 if problems else 0
    marker = TRACE_DIR / "TRACE_OK"
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != stamp():
        print("!! run `python3 -m patton_lab.p1v.runner trace` on this tree first")
        return 2
    if which == "study":
        from . import prereg
        if prereg.check() != 0:
            print("!! the pre-registration does not match the code; nothing ran")
            return 2
    arms = DEV_ARMS if which == "dev" else prompts_mod.STUDY_ARMS
    code = run_set(dataset, out, PROXY, Meter(LAB, LEDGER, SETS[which]), prompts=arms)
    print(f"{which}: {'complete' if code == 0 else 'stopped; run the same command again to resume'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
