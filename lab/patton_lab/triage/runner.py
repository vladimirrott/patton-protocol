"""Run the T1 analyst over the stored flags on Groq, record every call, and charge every dollar.

    python3 -m patton_lab.triage.runner plan                          # cells, calls and the spend bound
    python3 -m patton_lab.triage.runner trace                         # $0: a scripted upstream through every path
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.triage.runner dev     # paid: the development set
    lab/triage/with_groq_proxy.sh python3 -m patton_lab.triage.runner study   # paid: the registered test set
    python3 -m patton_lab.triage.runner replay --set dev|study        # re-decide every stored answer offline

A cell is one model with one context. Each call is one flag: the system prompt and the facts in, one JSON answer
out. Every attempt is stored in `lab/triage_<set>/<cell>.jsonl.gz` with the full response, and every response
that reports usage is charged to `lab/triage_spend.jsonl` at the model's price. Before a call starts, its worst
case (the prompt at two characters a token, plus the output cap) must fit under the set's cap and the lab's $40
cap on every ledger together; calls in flight hold their worst case until they settle.

A 429 waits and retries (Groq does not charge it). A 5xx or a lost connection retries twice, then stops the run,
and the same command resumes it, skipping stored flags. Any other refusal is stored as no answer, and no answer
escalates (rule 4). Paid commands refuse unless `trace` passed on this tree.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .. import ledgers
from . import flags as flags_mod
from . import prompt

LAB = Path(__file__).resolve().parents[2]
LEDGER = LAB / "triage_spend.jsonl"
TRACE_DIR = LAB / "triage_trace"
CONFIG = LAB / "triage" / "litellm.yaml"
PROXY = f"http://127.0.0.1:{os.environ.get('PATTON_TRIAGE_PROXY_PORT', '4001')}"
# name: (Groq id, $ per million input, cached input, output), read from console.groq.com on 2026-10-24
MODELS = {"gpt-oss-120b": ("openai/gpt-oss-120b", 0.15, 0.075, 0.60),
          "gpt-oss-20b": ("openai/gpt-oss-20b", 0.075, 0.037, 0.30),
          "gpt-oss-safeguard-20b": ("openai/gpt-oss-safeguard-20b", 0.075, 0.037, 0.30),
          "qwen3.8-27b": ("qwen/qwen3.8-27b", 0.80, 0.80, 4.00)}
CELLS = (("gpt-oss-120b", "blind"), ("gpt-oss-20b", "blind"), ("gpt-oss-safeguard-20b", "blind"),
         ("qwen3.8-27b", "blind"), ("gpt-oss-120b", "worker"), ("gpt-oss-safeguard-20b", "worker"),
         ("gpt-oss-120b", "inject"), ("gpt-oss-safeguard-20b", "inject"))
SETS = {"dev": (flags_mod.DEV_SOURCES, 1.00), "study": (flags_mod.TEST_SOURCES, 7.00)}  # sources, cap on the triage ledger (dev included)
MAX_OUT = 4096
REASONING = "medium"
PER_MODEL = 4  # calls in flight per model
TIMEOUT_S = 180


class SpendStop(Exception):
    pass


class UpstreamStop(Exception):
    pass


def cell_name(model: str, context: str) -> str:
    return f"{model}.{context}"


def cell_flags(dataset: list[flags_mod.Flag], context: str) -> list[flags_mod.Flag]:
    """blind: every flag; worker: flags with the worker's own words; inject: critical flags, where a miss costs."""
    if context == "worker":
        return [f for f in dataset if f.worker_text]
    if context == "inject":
        return [f for f in dataset if f.critical]
    return list(dataset)


def messages(flag: flags_mod.Flag, context: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": prompt.SYSTEM}, {"role": "user", "content": prompt.facts(flag, context)}]


def body(model: str, msgs: list[dict[str, str]]) -> dict[str, Any]:
    out = {"model": model, "messages": msgs, "max_tokens": MAX_OUT}
    if model.startswith("gpt-oss"):
        out["reasoning_effort"] = REASONING
    return out


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def usd(model: str, usage: dict[str, Any] | None) -> float | None:
    if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), int):
        return None
    _, price_in, price_cached, price_out = MODELS[model]
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    fresh = usage["prompt_tokens"] - cached
    return (fresh * price_in + cached * price_cached + int(usage.get("completion_tokens") or 0) * price_out) / 1e6


def bound(model: str, msgs: list[dict[str, str]]) -> float:
    _, price_in, _, price_out = MODELS[model]
    chars = sum(len(m["content"]) for m in msgs)
    return (chars / 2 * price_in + MAX_OUT * price_out) / 1e6


class Meter:
    """Holds the worst case of every call in flight, so parallel calls cannot pass a cap together."""

    def __init__(self, lab: Path, ledger: Path, cap: float) -> None:
        self.lab, self.ledger, self.cap = lab, ledger, cap
        self.lock = threading.Lock()
        self.held = 0.0

    def reserve(self, amount: float) -> None:
        with self.lock:
            spent = ledgers.spent_everywhere(self.lab)
            here = ledgers.ledger_sum(self.ledger)
            if here + self.held + amount > self.cap:
                raise SpendStop(f"the triage ledger holds ${here:.4f}; another call could pass this set's ${self.cap} cap")
            if sum(spent.values()) + self.held + amount > ledgers.GLOBAL_CAP:
                raise SpendStop(f"the lab has spent ${sum(spent.values()):.4f}; another call could pass the "
                                f"${ledgers.GLOBAL_CAP} cap")
            self.held += amount

    def settle(self, amount: float, charge_id: str, cost: float, usage: dict[str, Any] | None) -> None:
        with self.lock:
            self.held -= amount
            if cost:
                usage = usage or {}
                with open(self.ledger, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"id": charge_id, "usd": round(cost, 8), "in": usage.get("prompt_tokens"),
                                             "out": usage.get("completion_tokens")}) + "\n")


def post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None, str, float | None]:
    """(status, JSON body or None, error text, retry-after seconds)."""
    request = urllib.request.Request(url + "/v1/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return response.status, json.loads(response.read()), "", None
    except urllib.error.HTTPError as err:
        text = err.read()[:2000].decode("utf-8", "replace")
        after = err.headers.get("retry-after") if err.headers else None
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        return err.code, data, text, float(after) if after and after.replace(".", "", 1).isdigit() else None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
        return 0, None, f"{type(err).__name__}: {err}", None


def ask(url: str, model: str, context: str, flag: flags_mod.Flag, meter: Meter, stop: threading.Event,
        sleep=time.sleep, tag: str = "") -> dict[str, Any]:
    msgs = messages(flag, context)
    payload = body(model, msgs)
    attempts: list[dict[str, Any]] = []
    failures = 0
    while True:
        if stop.is_set():
            raise UpstreamStop("stopped by another call")
        worst = bound(model, msgs)
        meter.reserve(worst)
        started = time.time()
        status, data, error, after = post(url, payload)
        usage = (data or {}).get("usage") if status == 200 else None
        cost = usd(model, usage)
        if status == 200 and cost is None:
            cost = worst  # a success without usage is charged its worst case
        meter.settle(worst, f"{tag}{cell_name(model, context)}/{flag.id}/a{len(attempts)}", cost or 0.0, usage)
        attempts.append({"status": status, "wall_s": round(time.time() - started, 3), "usd": round(cost or 0.0, 8),
                         "usage": usage, "error": error[:500]})
        if status == 429:
            sleep(min(after or 10.0, 60.0))
            continue
        if status == 0 or status >= 500:
            failures += 1
            if failures > 2:
                stop.set()
                raise UpstreamStop(f"{flag.id}: {status} {error[:200]}")
            sleep(5.0 * failures)
            continue
        break
    content = None
    reasoning = None
    if status == 200:
        message = ((data or {}).get("choices") or [{}])[0].get("message") or {}
        content = message.get("content")
        reasoning = message.get("reasoning_content") or message.get("reasoning")
    decision = prompt.decide(flag, msgs[1]["content"], content)
    return {"flag": flag.id, "model": model, "context": context, "groq_model": MODELS[model][0],
            "messages_sha": sha(msgs), "payload_sha": sha(payload), "status": status, "content": content,
            "reasoning_chars": len(reasoning) if isinstance(reasoning, str) else None,
            "response": data if status == 200 else None, "attempts": attempts,
            "decision": {"action": decision.action, "why": decision.why},
            "critical": flag.critical, "usd": round(sum(a["usd"] for a in attempts), 8)}


def store_path(out: Path, model: str, context: str) -> Path:
    return out / f"{cell_name(model, context)}.jsonl.gz"


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def run_set(dataset: list[flags_mod.Flag], out: Path, url: str, meter: Meter, cells=CELLS,
            per_model: int = PER_MODEL, sleep=time.sleep) -> int:
    """Ask every pending (cell, flag). Returns 0 when all are stored, 2 after a stop (resumable)."""
    out.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    locks = {cell: threading.Lock() for cell in cells}
    gates = {model: threading.Semaphore(per_model) for model, _ in cells}
    problems: list[str] = []
    queues = []
    for model, context in cells:
        done = {r["flag"] for r in load(store_path(out, model, context))}
        queues.append([(model, context, f) for f in cell_flags(dataset, context) if f.id not in done])
    # round-robin over the cells, so every model has calls in flight at once
    jobs = [job for group in itertools.zip_longest(*queues) for job in group if job is not None]

    def one(job) -> None:
        model, context, flag = job
        if stop.is_set():
            return
        with gates[model]:
            try:
                record = ask(url, model, context, flag, meter, stop, sleep, f"{out.name}/")
            except (SpendStop, UpstreamStop) as err:
                stop.set()
                problems.append(str(err))
                return
        with locks[(model, context)]:
            with gzip.open(store_path(out, model, context), "at", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")

    with ThreadPoolExecutor(max_workers=per_model * len({m for m, _ in cells})) as pool:
        list(pool.map(one, jobs))
    for problem in sorted(set(problems))[:5]:
        print(f"!! {problem}")
    return 2 if problems else 0


def replay(dataset: list[flags_mod.Flag], out: Path, cells=CELLS) -> list[str]:
    """Re-decide every stored answer from the current code; any drift in the facts or the rules is a problem."""
    by_id = {f.id: f for f in dataset}
    problems = []
    for model, context in cells:
        records = load(store_path(out, model, context))
        expected = {f.id for f in cell_flags(dataset, context)}
        seen = [r["flag"] for r in records]
        if len(seen) != len(set(seen)):
            problems.append(f"{cell_name(model, context)}: a flag is stored twice")
        if set(seen) != expected:
            problems.append(f"{cell_name(model, context)}: {len(expected - set(seen))} flag(s) missing, "
                            f"{len(set(seen) - expected)} unknown")
        for record in records:
            flag = by_id.get(record["flag"])
            if flag is None:
                continue
            msgs = messages(flag, context)
            if record["messages_sha"] != sha(msgs):
                problems.append(f"{cell_name(model, context)}/{flag.id}: the facts changed since the call")
                continue
            again = prompt.decide(flag, msgs[1]["content"], record["content"])
            if {"action": again.action, "why": again.why} != record["decision"]:
                problems.append(f"{cell_name(model, context)}/{flag.id}: the stored decision differs from the rules")
            if record["critical"] != flag.critical:
                problems.append(f"{cell_name(model, context)}/{flag.id}: the label changed since the call")
    return problems


# ---- the trace: a scripted upstream, for $0 ------------------------------------------------------------

TRACED = ("flags.py", "prompt.py", "runner.py")


def stamp() -> str:
    here = Path(__file__).resolve().parent
    parts = [(here / name).read_bytes() for name in TRACED]
    parts.append(CONFIG.read_bytes())
    parts.append(flags_mod.GATE.read_bytes())
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def plan_lines(dataset: list[flags_mod.Flag], cap: float, cells=CELLS) -> list[str]:
    lines, total = [], 0.0
    for model, context in cells:
        chosen = cell_flags(dataset, context)
        worst = sum(bound(model, messages(f, context)) for f in chosen)
        total += worst
        lines.append(f"{cell_name(model, context):32} {len(chosen):5} calls  worst case ${worst:.4f}")
    lines.append(f"{'all cells':32} {sum(len(cell_flags(dataset, c)) for _, c in cells):5} calls  worst case "
                 f"${total:.4f}  (cap ${cap:.2f})")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.triage.runner")
    parser.add_argument("command", choices=("plan", "trace", "dev", "study", "replay"))
    parser.add_argument("--set", default="study", choices=tuple(SETS))
    args = parser.parse_args(argv)
    if args.command == "trace":
        from . import trace
        return trace.main(TRACE_DIR, stamp())
    which = args.set if args.command in ("plan", "replay") else args.command
    sources, cap = SETS[which]
    dataset = flags_mod.collect(sources).flags
    out = LAB / f"triage_{which}"
    if args.command == "plan":
        print("\n".join(plan_lines(dataset, cap)))
        return 0
    if args.command == "replay":
        problems = replay(dataset, out)
        print("\n".join(problems) if problems else f"replay OK: every stored answer in {out.name} re-decides the same")
        return 1 if problems else 0
    marker = TRACE_DIR / "TRACE_OK"
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != stamp():
        print("!! run `python3 -m patton_lab.triage.runner trace` on this tree first")
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
