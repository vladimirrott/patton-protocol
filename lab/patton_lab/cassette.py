"""Record and replay of model calls, keyed by a hash of the request.

A cassette is a gzip file of JSON lines, one gzip member per entry, so an
append never rewrites earlier entries. Each entry is `{"key": ..., **entry}`.

Record mode serves a recorded call from the file and appends new ones. Replay
mode never writes and never reaches the network: the caller asks `get`, and a
missing key is a miss. Misses are kept in `misses` because a caller may catch
the exception and carry on; `assert_no_misses` turns any miss back into a
failure at the end of a run.

A process killed during `put` can leave a truncated last member. Record mode
keeps every whole entry before it and rewrites the file without the broken
tail, so a resumed run continues. Replay mode refuses a damaged file, because a
replay that skips entries would report a study it did not reproduce.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import zlib
from pathlib import Path
from typing import Any, Literal

Mode = Literal["record", "replay"]


class CassetteMiss(Exception):
    """A replay asked for a call the cassette does not hold."""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def request_key(request: dict[str, Any]) -> str:
    """sha256 of the canonical request. Dict order cannot change the key."""
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()


def _read_entries(path: Path) -> tuple[list[dict[str, Any]], bool]:
    """Return (whole entries, damaged). `damaged` is True when the tail was cut."""
    entries: list[dict[str, Any]] = []
    damaged = False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            while True:
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    damaged = True
                    break
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    damaged = True
                    break
    except (EOFError, gzip.BadGzipFile, zlib.error, OSError):
        damaged = True
    return entries, damaged


class Cassette:
    def __init__(self, path: Path | str, mode: Mode) -> None:
        if mode not in ("record", "replay"):
            raise ValueError(f"unknown cassette mode {mode!r}")
        self.path = Path(path)
        self.mode = mode
        self.misses: list[str] = []
        self._entries: dict[str, dict[str, Any]] = {}

        if not self.path.exists():
            if mode == "replay":
                raise FileNotFoundError(f"replay needs a recorded cassette: {self.path}")
            return

        entries, damaged = _read_entries(self.path)
        if damaged and mode == "replay":
            raise ValueError(f"cassette {self.path} is damaged; replay refuses partial evidence")
        for entry in entries:
            self._entries.setdefault(entry["key"], entry)
        if damaged:
            self._rewrite()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return key in self._entries

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        return {k: v for k, v in entry.items() if k != "key"}

    def note_miss(self, key: str) -> None:
        self.misses.append(key)

    def assert_no_misses(self) -> None:
        if self.misses:
            raise CassetteMiss(f"{len(self.misses)} replay miss(es), first {self.misses[0][:12]}")

    def put(self, key: str, entry: dict[str, Any]) -> None:
        if self.mode != "record":
            raise RuntimeError("a replay cassette is read-only")
        if key in self._entries:
            return
        record = {"key": key, **entry}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(self.path, "at", encoding="utf-8") as handle:
            handle.write(canonical_json(record) + "\n")
            handle.flush()
        self._entries[key] = record

    def _rewrite(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as handle:
            for record in self._entries.values():
                handle.write(canonical_json(record) + "\n")
        os.replace(tmp, self.path)
