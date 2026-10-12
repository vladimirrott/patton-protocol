"""Record and replay for code execution, so a replay never depends on a clock.

Model-written code can print the time or a random number. Re-running it on
replay would change a tool result, the next prompt, and the cassette key, and
the replay would fail for no reason. So in record mode the first execution's
output is stored, keyed by the code and a hash of /work; replay serves it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable

from .cassette import Cassette, CassetteMiss, request_key


class ExecMemo:
    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    def run(self, kind: str, code: str, snapshot: dict[str, str], thunk: Callable[[], tuple[int, str]]) -> tuple[int, str]:
        state = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode("utf-8")).hexdigest()
        key = request_key({"kind": kind, "code": code, "work": state})
        hit = self.cassette.get(key)
        if hit is not None:
            return int(hit["rc"]), str(hit["output"])
        if self.cassette.mode == "replay":
            self.cassette.note_miss(key)
            raise CassetteMiss(f"no recorded {kind} execution for this code and state")
        rc, output = thunk()
        self.cassette.put(key, {"rc": rc, "output": output})
        return rc, output
