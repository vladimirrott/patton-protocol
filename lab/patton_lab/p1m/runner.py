"""Ask the frozen Verifier prompt of several Groq models over stored stop-arm inputs, recording every call.

    python3 -m patton_lab.p1m.runner plan --set dev|study
    python3 -m patton_lab.p1m.runner trace                      # $0: a scripted upstream through every path
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.p1m.runner dev|study
    python3 -m patton_lab.p1m.runner replay --set dev|study     # re-decide every stored answer offline

One call per case and arm, made the way the plugin makes the Verifier's call except for the arm's reasoning setting
and output budget. The plugin's `judge` turns the reply into a decision. Money and stops follow the P1V runner:
each call's worst case is held under the set's cap and under the lab-wide cap over every lab/*_spend.jsonl, a 429
waits, a 5xx retries twice and stops the run, and the same command resumes it.
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

from ..p1v import cases as cases_mod
from ..p1v import runner as p1v
from ..triage import runner as triage
from ..verify import runner as verify
from . import arms as arms_mod

LAB = cases_mod.LAB
LEDGER = LAB / "p1m_spend.jsonl"
TRACE_DIR = LAB / "p1m_trace"
PROXY = triage.PROXY
SETS = {"dev": 2.00, "study": 6.00}  # cap on this ledger while the set runs; the dev runs count toward the study's
PER_ARM = 4
DRAWS = {"dev": 2, "study": 2}  # draws per arm; the first decides the predictions
gate = cases_mod.gate
SpendStop = p1v.SpendStop
Meter = p1v.Meter


def draw_names(arms: dict[str, arms_mod.Arm], draws: int) -> dict[str, tuple[arms_mod.Arm, int]]:
    return {(a.name if n == 1 else f"{a.name}#{n}"): (a, n) for a in arms.values() for n in range(1, draws + 1)}


def payload(arm: arms_mod.Arm, msgs: list[dict[str, str]]) -> dict[str, Any]:
    return {"model": arm.model, "messages": msgs, "max_tokens": arm.max_tokens, **arm.extra()}


def bound(arm: arms_mod.Arm, msgs: list[dict[str, str]]) -> float:
    _, price_in, _, price_out = triage.MODELS[arm.model]
    return (sum(len(m["content"]) for m in msgs) / 2 * price_in + arm.max_tokens * price_out) / 1e6


def call(url: str, arm: arms_mod.Arm, msgs: list[dict[str, str]], meter: Meter, stop: threading.Event, tag: str,
         sleep=time.sleep) -> dict[str, Any]:
    body = payload(arm, msgs)
    attempts, failures = [], 0
    while True:
        if stop.is_set():
            raise triage.UpstreamStop("stopped by another call")
        worst = bound(arm, msgs)
        meter.reserve(worst)
        started = time.time()
        status, data, error, after = triage.post(url, body)
        usage = (data or {}).get("usage") if status == 200 else None
        cost = triage.usd(arm.model, usage)
        if status == 200 and cost is None:
            cost = worst
        meter.settle(worst, f"{tag}/a{len(attempts)}", cost or 0.0, usage)
        attempts.append({"status": status, "wall_s": round(time.time() - started, 3), "usd": round(cost or 0.0, 8),
                         "usage": usage, "error": error[:500]})
        if status == 429:
            sleep(min(after or 10.0, 60.0))
            continue
        if status == 0 or status >= 500:
            failures += 1
            if failures > 2:
                stop.set()
                raise triage.UpstreamStop(f"{tag}: {status} {error[:200]}")
            sleep(5.0 * failures)
            continue
        break
    choice = (((data or {}).get("choices") or [{}])[0]) if status == 200 else {}
    return {"status": status, "content": (choice.get("message") or {}).get("content"),
            "finish_reason": choice.get("finish_reason"), "attempts": attempts,
            "usd": round(sum(a["usd"] for a in attempts), 8)}


def ask(url: str, case: cases_mod.Case, name: str, arm: arms_mod.Arm, meter: Meter, stop: threading.Event,
        sleep=time.sleep, tag: str = "") -> dict[str, Any]:
    msgs = verify.messages(arms_mod.SYSTEM, case.source)
    reply = call(url, arm, msgs, meter, stop, f"{tag}{name}/{case.id}", sleep)
    content = reply["content"] if reply["status"] == 200 else None
    return {"case": case.id, "arm": name, "kind": case.kind, "fixture": case.fixture, "harness": case.harness,
            "input_sha": p1v.sha(payload(arm, msgs)), "reply": reply, **p1v.decide(content, case.source),
            "usd": reply["usd"]}


store_path = p1v.store_path
load = p1v.load


def run_set(dataset: list[cases_mod.Case], out: Path, url: str, meter: Meter,
            names: dict[str, tuple[arms_mod.Arm, int]], per_arm: int = PER_ARM, sleep=time.sleep) -> int:
    out.mkdir(parents=True, exist_ok=True)
    stop, lock, gate_ = threading.Event(), threading.Lock(), threading.Semaphore(per_arm)
    problems: list[str] = []
    jobs = []
    for name in names:
        done = {r["case"] for r in load(store_path(out, name))}
        jobs += [(name, c) for c in dataset if c.id not in done]

    def one(job) -> None:
        name, case = job
        if stop.is_set():
            return
        with gate_:
            try:
                record = ask(url, case, name, names[name][0], meter, stop, sleep, f"{out.name}/")
            except (SpendStop, triage.SpendStop, triage.UpstreamStop) as err:
                stop.set()
                problems.append(str(err))
                return
        with lock:
            with gzip.open(store_path(out, name), "at", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    with ThreadPoolExecutor(max_workers=per_arm) as pool:
        list(pool.map(one, jobs))
    for problem in sorted(set(problems))[:5]:
        print(f"!! {problem}")
    return 2 if problems else 0


def replay(dataset: list[cases_mod.Case], out: Path, names: dict[str, tuple[arms_mod.Arm, int]]) -> list[str]:
    by_id = {c.id: c for c in dataset}
    problems = []
    for name, (arm, _) in names.items():
        records = load(store_path(out, name))
        seen = [r["case"] for r in records]
        if len(seen) != len(set(seen)):
            problems.append(f"{name}: a case is stored twice")
        if set(seen) != set(by_id):
            problems.append(f"{name}: {len(set(by_id) - set(seen))} case(s) missing, {len(set(seen) - set(by_id))} unknown")
        for record in records:
            case = by_id.get(record["case"])
            if case is None:
                continue
            if record["input_sha"] != p1v.sha(payload(arm, verify.messages(arms_mod.SYSTEM, case.source))):
                problems.append(f"{name}/{case.id}: the inputs changed since the call")
                continue
            reply = record["reply"]
            again = p1v.decide(reply["content"] if reply["status"] == 200 else None, case.source)
            if any(again[k] != record[k] for k in again):
                problems.append(f"{name}/{case.id}: the stored decision differs from the rules")
            if record["kind"] != case.kind:
                problems.append(f"{name}/{case.id}: the truth changed since the call")
    return problems


TRACED = ("arms.py", "runner.py")


def stamp() -> str:
    here = Path(__file__).resolve().parent
    parts = [(here / n).read_bytes() for n in TRACED] + [
        cases_mod.GATE_PATH.read_bytes(), triage.CONFIG.read_bytes(), (here.parent / "p1v" / "runner.py").read_bytes(),
        (here.parent / "p1v" / "prompts.py").read_bytes(), (here.parent / "spend_all.py").read_bytes()]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def datasets(which: str) -> list[cases_mod.Case]:
    return p1v.datasets(which)


def plan_lines(dataset: list[cases_mod.Case], cap: float, names) -> list[str]:
    lines, total = [], 0.0
    for name, (arm, _) in names.items():
        worst = sum(bound(arm, verify.messages(arms_mod.SYSTEM, c.source)) for c in dataset)
        total += worst
        lines.append(f"{name:14} {len(dataset):4} calls  worst case ${worst:.2f}")
    lines.append(f"{'all':14} {len(dataset) * len(names):4} calls  worst case ${total:.2f}  (cap ${cap:.2f}; a call "
                 f"is charged what it uses, so the worst case is a bound, not a forecast)")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.p1m.runner")
    parser.add_argument("command", choices=("plan", "trace", "dev", "study", "replay"))
    parser.add_argument("--set", default="study", choices=tuple(SETS))
    parser.add_argument("--arms", default="", help="comma list of arm names; default all (dev runs only)")
    args = parser.parse_args(argv)
    if args.command == "trace":
        from . import trace
        return trace.main(TRACE_DIR, stamp())
    which = args.set if args.command in ("plan", "replay") else args.command
    pool = arms_mod.ALL if which == "dev" else arms_mod.ARMS
    chosen = {n: a for n, a in pool.items() if not args.arms or n in args.arms.split(",")}
    names = draw_names(chosen, DRAWS[which])
    dataset = datasets(which)
    out = LAB / f"p1m_{which}"
    if args.command == "plan":
        print("\n".join(plan_lines(dataset, SETS[which], names)))
        return 0
    if args.command == "replay":
        problems = replay(dataset, out, names)
        print("\n".join(problems) if problems else f"replay OK: every stored answer in {out.name} re-decides the same")
        return 1 if problems else 0
    marker = TRACE_DIR / "TRACE_OK"
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != stamp():
        print("!! run `python3 -m patton_lab.p1m.runner trace` on this tree first")
        return 2
    if which == "study":
        from . import prereg
        if prereg.check() != 0 or args.arms:
            print("!! the pre-registration does not match the code, or --arms was given; nothing ran")
            return 2
    code = run_set(dataset, out, PROXY, Meter(LAB, LEDGER, SETS[which]), names)
    print(f"{which}: {'complete' if code == 0 else 'stopped; run the same command again to resume'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
