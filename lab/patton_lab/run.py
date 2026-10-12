"""Command line for the Lab.

    python3 -m patton_lab.run dry-run  [--exp E1 ...]
    python3 -m patton_lab.run controls
    python3 -m patton_lab.run smoke    --provider groq|openai
    python3 -m patton_lab.run record   --exp E1 [--limit N]
    python3 -m patton_lab.run replay   [--exp E1 ...]
    python3 -m patton_lab.run report
    python3 -m patton_lab.run providers --exp E1 ...

Exit codes: 0 ok, 1 a check failed (replay mismatch, control off its score),
2 the dry run refuses the spend, 3 a provider or the meter stopped a record run,
4 a prerequisite is missing (isolated runner, key).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import cost, isolate
from .cassette import Cassette, CassetteMiss
from .client import PROVIDERS, AuthError, Client, HttpTransport, Provider, ProviderError, QuotaError, RateLimitError
from .controls import E1_CONTROLS
from .cost import BudgetExceeded, Meter
from .experiments import E1_TURNS, EXPERIMENTS, SPEND_ORDER, MAX_TURNS, Job, calls_multiplier, execute_job, first_prompt, jobs_for
from .fixtures import e1
from .memo import ExecMemo
from .sandbox import Sandbox, tool_schemas
from .score import score_e1

LAB_ROOT = Path(__file__).resolve().parents[1]
CAP_USD = 10.0
ABORT_AT_USD = 9.0
REPLAY_FIELDS = ("metrics", "final_text", "turns", "stop_reason", "events")
EXPECTED_GROWTH = 400
WORST_GROWTH = 1000
OUT_PER_TURN = {"groq": 600, "openai": 150}
MAX_TOKENS = 2000


class Paths:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.results = root / "results"
        self.cassettes = root / "cassettes"
        self.ledger = self.results / "spend.json"

    def results_file(self, exp: str) -> Path:
        return self.results / f"{exp}.jsonl"

    def cassette(self, exp: str) -> Path:
        return self.cassettes / f"{exp}.jsonl.gz"

    def exec_cassette(self, exp: str) -> Path:
        return self.cassettes / f"{exp}.exec.jsonl.gz"

    @property
    def lock(self) -> Path:
        return self.results / ".record.lock"


class LockHeld(Exception):
    pass


@contextmanager
def exclusive(paths: Paths):
    """Hold one lock for a whole record or smoke command, so two runs never spend in parallel."""
    paths.results.mkdir(parents=True, exist_ok=True)
    handle = paths.lock.open("w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(f"another record or smoke run holds {paths.lock}") from None
        yield
    finally:
        handle.close()


def load_results(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number} is not valid JSON") from exc
        out[record["job"]] = record
    return out


def append_result(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()


def parse_job(job_id: str) -> Job:
    exp, arm, case, rep, provider_model = job_id.split("|")
    provider, model = provider_model.split("/", 1)
    return Job(exp, arm, case, int(rep.lstrip("r")), provider, model)


def _summary(record: dict[str, Any]) -> str:
    metrics = record.get("metrics", {})
    keys = [k for k in ("harm", "utility", "detected", "decision", "delegated", "hidden_pass", "overrun",
                        "fabrication", "lost_updates") if k in metrics]
    return " ".join(f"{k}={metrics[k]}" for k in keys)


# ---- dry run -------------------------------------------------------------------


def estimate(job: Job) -> tuple[float, float]:
    """(expected, worst-case) USD for one job."""
    exp = EXPERIMENTS.get(job.exp)
    system, user, tools = first_prompt(job)
    first = cost.estimate_tokens([{"role": "system", "content": system}, {"role": "user", "content": user},
                                  tool_schemas(tools)])
    price = cost.price(job.provider, job.model)
    loops = calls_multiplier(job)
    turns = exp.turns_expected if exp else 6
    worst_turns = 1 if (exp and exp.single_call) else (E1_TURNS if job.exp in ("E1", "E1E", "E1R", "E1H", "SMOKE") else MAX_TURNS)
    expected_in, expected_out = cost.run_tokens(first, turns, EXPECTED_GROWTH, OUT_PER_TURN[job.provider])
    worst_in, worst_out = cost.run_tokens(first, worst_turns, WORST_GROWTH, MAX_TOKENS)
    return (loops * cost.usd(expected_in, expected_out, price), loops * cost.usd(worst_in, worst_out, price))


def cmd_dry_run(paths: Paths, exps: list[str]) -> int:
    meter = Meter(paths.ledger, ABORT_AT_USD, CAP_USD)
    total_expected = 0.0
    total_worst = 0.0
    print(f"{'experiment':10s} {'pending':>8s} {'expected $':>11s} {'worst $':>9s}")
    for name in exps:
        done = load_results(paths.results_file(name))
        pending = [job for job in jobs_for(name) if job.id not in done]
        expected = sum(estimate(job)[0] for job in pending)
        worst = sum(estimate(job)[1] for job in pending)
        total_expected += expected
        total_worst += worst
        print(f"{name:10s} {len(pending):8d} {expected:11.2f} {worst:9.2f}")
    spent = meter.spent()
    print(f"{'total':10s} {'':8s} {total_expected:11.2f} {total_worst:9.2f}")
    print(f"actual spend so far ${spent:.4f}; cap ${CAP_USD:.2f}; meter aborts at ${ABORT_AT_USD:.2f}")
    if spent + total_expected > CAP_USD:
        print(f"REFUSED: expected bound ${total_expected:.2f} plus spend ${spent:.2f} passes the ${CAP_USD:.2f} cap")
        return 2
    print("the expected bound fits under the cap; the meter enforces the abort line on actual spend")
    return 0


# ---- controls ------------------------------------------------------------------


def cmd_controls() -> int:
    failures = 0
    for name, agent in E1_CONTROLS.items():
        harms, utilities = [], []
        for fixture in e1.E1_FIXTURES + e1.E1_HELDOUT:
            with Sandbox(fixture.files) as box:
                before = box.snapshot()
                agent(box, fixture)
                result = score_e1(fixture, before, box.snapshot(), box.events, box.approval_requests, "")
            harms.append(result["harm"])
            utilities.append(result["utility"])
            expected = {"do_nothing": (0, 0.0), "oracle": (0, 1.0)}.get(name)
            if expected and (result["harm"], result["utility"]) != expected:
                failures += 1
                print(f"!! {name} on {fixture.id}: harm={result['harm']} utility={result['utility']}, expected {expected}")
            if name == "delete_everything" and result["harm"] != 1:
                failures += 1
                print(f"!! delete_everything on {fixture.id} scored harm 0")
        print(f"{name:18s} mean harm {sum(harms) / len(harms):.2f}  mean utility {sum(utilities) / len(utilities):.2f}")
    if failures:
        print(f"CONTROLS FAILED: {failures} score(s) off their known value; do not run the study")
        return 1
    print("controls OK: every scripted agent landed on its known score")
    return 0


# ---- record and replay -----------------------------------------------------------


def _transports(names: set[str], providers: dict[str, Provider]) -> dict[str, HttpTransport]:
    return {name: HttpTransport(providers[name]) for name in sorted(names)}


def _record_jobs(paths: Paths, name: str, jobs: list[Job], providers: dict[str, Provider], meter: Meter) -> int:
    exp = EXPERIMENTS.get(name)
    if exp and exp.needs_isolation and not isolate.available():
        print(f"!! {name} needs the isolated runner (podman and the pinned image); refusing to start")
        return 4
    results_path = paths.results_file(name)
    done = load_results(results_path)
    pending = [job for job in jobs if job.id not in done]
    if not pending:
        print(f"{name}: nothing pending")
        return 0
    try:
        transports = _transports({job.provider for job in pending}, providers)
    except AuthError as exc:
        print(f"!! {exc}")
        return 4
    client = Client(Cassette(paths.cassette(name), "record"), transports, meter)
    memo = ExecMemo(Cassette(paths.exec_cassette(name), "record"))
    for index, job in enumerate(pending, start=1):
        spent_before = meter.spent()
        try:
            record = execute_job(job, client, providers, memo)
        except (AuthError, QuotaError, RateLimitError, ProviderError, BudgetExceeded) as exc:
            print(f"!! stopped at {job.id}: {type(exc).__name__}: {exc}")
            print(f"   {index - 1} of {len(pending)} pending jobs finished; spend ${meter.spent():.4f}")
            return 3
        except isolate.IsolationError as exc:
            print(f"!! stopped at {job.id}: the isolated runner failed: {exc}")
            return 4
        meter.reload()
        record["estimated_usd"] = round(estimate(job)[0], 6) if job.exp in EXPERIMENTS else None
        record["actual_usd"] = round(meter.spent() - spent_before, 6)
        append_result(results_path, record)
        print(f"[{index}/{len(pending)}] {job.id} {_summary(record)} live={record['calls_live']} "
              f"spent=${meter.spent():.4f}", flush=True)
    return 0


def _frozen(paths: Paths) -> bool:
    """A paid run on the study's own results directory needs a current pre-registration."""
    if paths.root.resolve() != LAB_ROOT.resolve():
        return True
    from .prereg import PREREG_PATH, prereg_ok

    if prereg_ok():
        return True
    print(f"!! {PREREG_PATH} is missing or does not match the code; freeze it before spending")
    return False


def cmd_record(paths: Paths, exps: list[str], limit: int | None, providers: dict[str, Provider]) -> int:
    if not _frozen(paths):
        return 4
    try:
        with exclusive(paths):
            meter = Meter(paths.ledger, ABORT_AT_USD, CAP_USD)
            for name in exps:
                jobs = jobs_for(name)
                if limit is not None:
                    done = load_results(paths.results_file(name))
                    jobs = [job for job in jobs if job.id not in done][:limit]
                rc = _record_jobs(paths, name, jobs, providers, meter)
                if rc:
                    return rc
            print(f"record finished; actual spend ${meter.spent():.4f}")
            return 0
    except LockHeld as exc:
        print(f"!! {exc}; refusing to start")
        return 4


def cmd_smoke(paths: Paths, provider: str, providers: dict[str, Provider]) -> int:
    """One real job with a fresh salt, so it can only pass by reaching the provider."""
    if not _frozen(paths):
        return 4
    model = {"groq": "openai/gpt-oss-120b", "openai": "gpt-4.1-mini"}[provider]
    job = Job("SMOKE", "B", "f01", int(time.time() * 1000), provider, model)
    try:
        with exclusive(paths):
            meter = Meter(paths.ledger, ABORT_AT_USD, CAP_USD)
            rc = _record_jobs(paths, "SMOKE", [job], providers, meter)
    except LockHeld as exc:
        print(f"!! {exc}; refusing to start")
        return 4
    if rc:
        return rc
    record = load_results(paths.results_file("SMOKE"))[job.id]
    print(json.dumps({k: record.get(k) for k in ("turns", "stop_reason", "usage", "calls_live", "models_returned",
                                                   "tool_use_failures", "actual_usd", "metrics")}, indent=2))
    if record["calls_live"] < 1:
        print("SMOKE FAILED: no call reached the provider")
        return 1
    return 0


def _comparable(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record.get(key) for key in REPLAY_FIELDS}


def cmd_replay(paths: Paths, exps: list[str], providers: dict[str, Provider]) -> int:
    problems = 0
    checked = 0
    for name in exps:
        recorded = load_results(paths.results_file(name))
        if not recorded:
            print(f"!! {name}: no recorded results to replay")
            problems += 1
            continue
        try:
            cassette = Cassette(paths.cassette(name), "replay")
        except (FileNotFoundError, ValueError) as exc:
            print(f"!! {name}: {exc}")
            problems += 1
            continue
        exec_path = paths.exec_cassette(name)
        memo = ExecMemo(Cassette(exec_path, "replay")) if exec_path.exists() else None
        client = Client(cassette, {}, Meter(paths.root / "results" / ".replay-meter.json", ABORT_AT_USD, CAP_USD))
        mismatches = 0
        for job_id, stored in sorted(recorded.items()):
            job = parse_job(job_id)
            try:
                fresh = execute_job(job, client, providers, memo)
            except CassetteMiss as exc:
                mismatches += 1
                print(f"!! {job_id}: cassette miss ({exc})")
                continue
            if _comparable(fresh) != _comparable(stored):
                mismatches += 1
                diff = [k for k in REPLAY_FIELDS if fresh.get(k) != stored.get(k)]
                print(f"!! {job_id}: replay differs in {', '.join(diff)}")
            checked += 1
        try:
            cassette.assert_no_misses()
            if memo is not None:
                memo.cassette.assert_no_misses()
        except CassetteMiss as exc:
            mismatches += 1
            print(f"!! {name}: {exc}")
        print(f"{name}: {len(recorded)} recorded, {mismatches} problem(s)")
        problems += mismatches
    (paths.root / "results" / ".replay-meter.json").unlink(missing_ok=True)
    if checked == 0:
        print("REPLAY CHECKED NOTHING: no recorded job was replayed")
        return 1
    if problems:
        print(f"REPLAY FAILED: {problems} problem(s)")
        return 1
    print(f"replay OK: {checked} job(s) reproduce exactly with no network")
    return 0


def cmd_providers(exps: list[str]) -> int:
    print(" ".join(sorted({EXPERIMENTS[name].provider[0] for name in exps})))
    return 0


def main(argv: list[str] | None = None, providers: dict[str, Provider] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="patton_lab.run")
    parser.add_argument("command", choices=["dry-run", "controls", "smoke", "record", "replay", "report", "providers"])
    parser.add_argument("--exp", nargs="*", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="groq")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)

    paths = Paths(root or args.root or LAB_ROOT)
    providers = providers or PROVIDERS
    exps = args.exp or list(SPEND_ORDER)
    unknown = [name for name in exps if name not in EXPERIMENTS and name != "SMOKE"]
    if unknown:
        print(f"unknown experiment(s): {', '.join(unknown)}")
        return 1

    if args.command == "dry-run":
        return cmd_dry_run(paths, exps)
    if args.command == "controls":
        return cmd_controls()
    if args.command == "smoke":
        return cmd_smoke(paths, args.provider, providers)
    if args.command == "record":
        if not args.exp:
            print("record needs --exp, so a single command cannot start the whole study by accident")
            return 1
        return cmd_record(paths, exps, args.limit, providers)
    if args.command == "replay":
        return cmd_replay(paths, exps if args.exp else [n for n in list(SPEND_ORDER) + ["SMOKE"]
                                                        if paths.results_file(n).exists()], providers)
    if args.command == "providers":
        return cmd_providers(exps)
    if args.command == "report":
        from .report import write_report

        return write_report(paths.root)
    return 1


if __name__ == "__main__":
    sys.exit(main())
