"""Replay the plugin's claim reader and blind Verifier over the stored gate-study runs on Groq, recording every call.

    python3 -m patton_lab.verify.runner plan                          # cells, calls and the spend bound
    python3 -m patton_lab.verify.runner trace                         # $0: a scripted upstream through every path
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.verify.runner dev     # paid: the development set
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.verify.runner study   # paid: the registered test set
    python3 -m patton_lab.verify.runner replay --set dev|study        # re-decide every stored answer offline

Each case costs two calls, made the way the plugin makes them (its system prompts, its inputs, max_tokens 4096, no
reasoning setting): the claim reader on the request and the final message, and the Verifier on the request, the
notes, and the change. The replay always calls the Verifier, even when the claim reader says the worker claimed
nothing, so the Verifier's accuracy is measured on every run; the hook's end-to-end decision is then computed
from both answers by the plugin's own rules.

Money and stops work as in the triage runner: every response that reports usage is charged to
lab/verify_spend.jsonl, each call's worst case is held under the set cap and the $40 lab-wide cap
(`patton_lab.spend` counts every ledger), a 429 waits, a 5xx retries twice and then stops the run, the same command
resumes it, and paid commands refuse unless `trace` passed on this tree.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import spend
from ..triage import runner as triage
from . import cases as cases_mod

LAB = cases_mod.LAB
LEDGER = LAB / "verify_spend.jsonl"
TRACE_DIR = LAB / "verify_trace"
PROXY = triage.PROXY
MODELS = ("gpt-oss-120b", "qwen3.8-27b")
SETS = {"dev": (cases_mod.DEV_SOURCES, 0.50), "study": (cases_mod.TEST_SOURCES, 4.00)}  # cap on this ledger
PER_MODEL = 4
gate = cases_mod.gate


class SpendStop(Exception):
    pass


class Meter(triage.Meter):
    """The triage meter, counting every ledger the lab has (patton_lab.spend), this one included."""

    def reserve(self, amount: float) -> None:
        with self.lock:
            spent = spend.spent_everywhere(self.lab)
            here = spend.ledgers.ledger_sum(self.ledger)
            if here + self.held + amount > self.cap:
                raise SpendStop(f"the verify ledger holds ${here:.4f}; another call could pass this set's ${self.cap} cap")
            if sum(spent.values()) + self.held + amount > spend.GLOBAL_CAP:
                raise SpendStop(f"the lab has spent ${sum(spent.values()):.4f}; another call could pass the "
                                f"${spend.GLOBAL_CAP} cap")
            self.held += amount


def messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def body(model: str, msgs: list[dict[str, str]]) -> dict[str, Any]:
    return {"model": model, "messages": msgs, "max_tokens": triage.MAX_OUT}


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def call(url: str, model: str, msgs: list[dict[str, str]], meter: Meter, stop: threading.Event, tag: str,
         sleep=time.sleep) -> dict[str, Any]:
    """One call with the triage runner's retry rules. Returns {status, content, attempts, usd}."""
    payload = body(model, msgs)
    attempts, failures = [], 0
    while True:
        if stop.is_set():
            raise triage.UpstreamStop("stopped by another call")
        worst = triage.bound(model, msgs)
        meter.reserve(worst)
        started = time.time()
        status, data, error, after = triage.post(url, payload)
        usage = (data or {}).get("usage") if status == 200 else None
        cost = triage.usd(model, usage)
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
    content = None
    if status == 200:
        content = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content")
    return {"status": status, "content": content, "attempts": attempts,
            "usd": round(sum(a["usd"] for a in attempts), 8)}


def decide(claim_content: str | None, verifier_content: str | None, source: str) -> dict[str, Any]:
    """The plugin's rules on both answers: what the Verifier found, and what the hook would have done."""
    claims = gate.read_claim(claim_content)
    verdict, findings, judged = gate.judge(verifier_content, source)
    final = judged if claims else "allow (no completion claimed)"
    return {"claims_done": claims, "verdict": verdict, "findings": findings, "verifier_decision": judged,
            "decision": final}


def ask(url: str, model: str, case: cases_mod.Case, meter: Meter, stop: threading.Event, sleep=time.sleep,
        tag: str = "") -> dict[str, Any]:
    claim_msgs = messages(gate.CLAIMS_SYSTEM, cases_mod.claims_text(case))
    source = cases_mod.verifier_text(case)
    verify_msgs = messages(gate.VERIFIER_SYSTEM, source)
    claim = call(url, model, claim_msgs, meter, stop, f"{tag}{model}/{case.id}/claims", sleep)
    verdict = call(url, model, verify_msgs, meter, stop, f"{tag}{model}/{case.id}/verifier", sleep)
    return {"case": case.id, "model": model, "kind": case.kind, "fixture": case.fixture, "arm": case.arm,
            "claims_sha": sha(claim_msgs), "verifier_sha": sha(verify_msgs),
            "claims": claim, "verifier": verdict,
            **decide(claim["content"] if claim["status"] == 200 else None,
                     verdict["content"] if verdict["status"] == 200 else None, source),
            "usd": round(claim["usd"] + verdict["usd"], 8)}


def store_path(out: Path, model: str) -> Path:
    return out / f"{model}.jsonl.gz"


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_set(dataset: list[cases_mod.Case], out: Path, url: str, meter: Meter, models=MODELS,
            per_model: int = PER_MODEL, sleep=time.sleep) -> int:
    out.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    locks = {m: threading.Lock() for m in models}
    gates = {m: threading.Semaphore(per_model) for m in models}
    problems: list[str] = []
    queues = []
    for model in models:
        done = {r["case"] for r in load(store_path(out, model))}
        queues.append([(model, c) for c in dataset if c.id not in done])
    jobs = [job for group in itertools.zip_longest(*queues) for job in group if job is not None]

    def one(job) -> None:
        model, case = job
        if stop.is_set():
            return
        with gates[model]:
            try:
                record = ask(url, model, case, meter, stop, sleep, f"{out.name}/")
            except (SpendStop, triage.SpendStop, triage.UpstreamStop) as err:
                stop.set()
                problems.append(str(err))
                return
        with locks[model]:
            with gzip.open(store_path(out, model), "at", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    with ThreadPoolExecutor(max_workers=per_model * len(models)) as pool:
        list(pool.map(one, jobs))
    for problem in sorted(set(problems))[:5]:
        print(f"!! {problem}")
    return 2 if problems else 0


def replay(dataset: list[cases_mod.Case], out: Path, models=MODELS) -> list[str]:
    by_id = {c.id: c for c in dataset}
    problems = []
    for model in models:
        records = load(store_path(out, model))
        seen = [r["case"] for r in records]
        if len(seen) != len(set(seen)):
            problems.append(f"{model}: a case is stored twice")
        if set(seen) != set(by_id):
            problems.append(f"{model}: {len(set(by_id) - set(seen))} case(s) missing, {len(set(seen) - set(by_id))} unknown")
        for record in records:
            case = by_id.get(record["case"])
            if case is None:
                continue
            source = cases_mod.verifier_text(case)
            if record["claims_sha"] != sha(messages(gate.CLAIMS_SYSTEM, cases_mod.claims_text(case))) or \
                    record["verifier_sha"] != sha(messages(gate.VERIFIER_SYSTEM, source)):
                problems.append(f"{model}/{case.id}: the inputs changed since the call")
                continue
            again = decide(record["claims"]["content"] if record["claims"]["status"] == 200 else None,
                           record["verifier"]["content"] if record["verifier"]["status"] == 200 else None, source)
            if any(again[k] != record[k] for k in again):
                problems.append(f"{model}/{case.id}: the stored decision differs from the rules")
            if record["kind"] != case.kind:
                problems.append(f"{model}/{case.id}: the truth changed since the call")
    return problems


TRACED = ("cases.py", "runner.py")


def stamp() -> str:
    here = Path(__file__).resolve().parent
    parts = [(here / name).read_bytes() for name in TRACED] + [cases_mod.GATE_PATH.read_bytes(),
                                                                triage.CONFIG.read_bytes()]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def plan_lines(dataset, cap: float, models=MODELS) -> list[str]:
    lines, total = [], 0.0
    for model in models:
        worst = sum(triage.bound(model, messages(gate.CLAIMS_SYSTEM, cases_mod.claims_text(c))) +
                    triage.bound(model, messages(gate.VERIFIER_SYSTEM, cases_mod.verifier_text(c))) for c in dataset)
        total += worst
        lines.append(f"{model:16} {2 * len(dataset):5} calls  worst case ${worst:.4f}")
    lines.append(f"{'all':16} {2 * len(dataset) * len(models):5} calls  worst case ${total:.4f}  (cap ${cap:.2f})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.verify.runner")
    parser.add_argument("command", choices=("plan", "trace", "dev", "study", "replay"))
    parser.add_argument("--set", default="study", choices=tuple(SETS))
    args = parser.parse_args(argv)
    if args.command == "trace":
        from . import trace
        return trace.main(TRACE_DIR, stamp())
    which = args.set if args.command in ("plan", "replay") else args.command
    sources, cap = SETS[which]
    dataset = cases_mod.collect(sources)
    out = LAB / f"verify_{which}"
    if args.command == "plan":
        print("\n".join(plan_lines(dataset, cap)))
        return 0
    if args.command == "replay":
        problems = replay(dataset, out)
        print("\n".join(problems) if problems else f"replay OK: every stored answer in {out.name} re-decides the same")
        return 1 if problems else 0
    marker = TRACE_DIR / "TRACE_OK"
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != stamp():
        print("!! run `python3 -m patton_lab.verify.runner trace` on this tree first")
        return 2
    if which == "study":
        from . import prereg
        if prereg.check() != 0:
            print("!! the pre-registration does not match the code; nothing ran")
            return 2
    code = run_set(dataset, out, PROXY, Meter(LAB, LEDGER, cap))
    print(f"{which}: {'complete' if code == 0 else 'stopped; run the same command again to resume'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
