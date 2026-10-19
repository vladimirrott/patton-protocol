"""Spend for the gate study, and the run cap for the subscription backend.

The 2026-10-12 ledger and the pilot records count as prior spend, so the cap covers both studies. Prices are
the lab's, and cached prompt tokens are charged in full, which overstates the bill and never understates it.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import cost

EPSILON = 1e-9
GROQ_PRICE = cost.price("groq", "openai/gpt-oss-120b")
SUBSCRIPTION_RUN_CAP = 500


class CapExceeded(Exception):
    pass


class Meter:
    def __init__(self, path: Path | str, lab_ledger: Path | str, pilot_dirs: list[Path], cap: float = 15.0,
                 abort: float = 14.0) -> None:
        if abort > cap:
            raise ValueError(f"abort line {abort} is above the cap {cap}")
        self.path, self.lab_ledger, self.pilot_dirs = Path(path), Path(lab_ledger), [Path(d) for d in pilot_dirs]
        self.cap, self.abort = cap, abort

    def prior(self) -> float:
        try:
            table = json.loads(self.lab_ledger.read_text(encoding="utf-8"))["spent"]
            lab = float(sum(table.values()))
        except (OSError, ValueError, KeyError, TypeError) as err:
            raise ValueError(f"spend ledger {self.lab_ledger} is unreadable; refusing to guess the spend") from err
        pilot = 0.0
        for directory in self.pilot_dirs:
            for record in sorted(directory.glob("*.json")):
                usage = json.loads(record.read_text(encoding="utf-8")).get("usage") or {}
                tokens_in = int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0))
                pilot += cost.usd(tokens_in, int(usage.get("output_tokens", 0)), GROQ_PRICE)
        return lab + pilot

    def spent(self) -> float:
        if not self.path.exists():
            return 0.0
        return sum(json.loads(line)["usd"] for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip())

    def total(self) -> float:
        return self.prior() + self.spent()

    def charge(self, record_id: str, input_tokens: int, output_tokens: int, price: tuple[float, float] = GROQ_PRICE) -> float:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("a charge cannot be negative")
        if self.path.exists() and any(json.loads(line)["id"] == record_id
                                      for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()):
            raise ValueError(f"{record_id} was already charged")
        amount = cost.usd(input_tokens, output_tokens, price)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": record_id, "usd": amount, "in": input_tokens, "out": output_tokens}) + "\n")
        return amount

    def check(self, bound: float = 0.0) -> None:
        total = self.total()
        if total + bound >= self.abort - EPSILON:
            raise CapExceeded(f"spent ${total:.4f} plus a bound of ${bound:.4f} reaches the abort line ${self.abort:.2f}")


def live_runs(out_dir: Path, backend: str) -> int:
    return sum(1 for p in Path(out_dir).glob("*.json") if json.loads(p.read_text(encoding="utf-8")).get("backend") == backend)


def check_run_cap(out_dir: Path, backend: str, limit: int = SUBSCRIPTION_RUN_CAP) -> None:
    count = live_runs(out_dir, backend)
    if count >= limit:
        raise CapExceeded(f"{count} live {backend} runs recorded, which reaches the cap of {limit}")
