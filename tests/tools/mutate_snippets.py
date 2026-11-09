#!/usr/bin/env python3
"""Apply one exact-snippet mutant at a time to a file, run a test command, and restore the file from a pristine copy.

    python3 tests/tools/mutate_snippets.py TARGET MUTANTS.py -- python3 -m unittest test_x     (run from tests/)

MUTANTS.py defines MUTANTS = [(name, old, new), ...]. Outcomes: KILLED (the command failed), SURVIVED (it passed),
NOT APPLIED (`old` does not occur exactly once), CAPPED (time or address-space cap; never counts as a kill). The file is
put back from a copy taken at the start and its hash checked after every mutant; bytecode caches are cleared and the
run waits past the second, so a same-size edit never runs stale bytecode.
"""

import hashlib
import os
import resource
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cap() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (6 << 30, 6 << 30))


def main() -> int:
    target = Path(sys.argv[1]).resolve()
    mutants = runpy.run_path(sys.argv[2])["MUTANTS"]
    cmd = sys.argv[sys.argv.index("--") + 1:]
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        pristine = Path(tmp) / "pristine"
        shutil.copy(target, pristine)
        digest = sha(pristine)
        try:
            for name, old, new in mutants:
                text = pristine.read_text()
                if text.count(old) != 1:
                    results.append((name, f"NOT APPLIED ({text.count(old)} matches)"))
                    continue
                target.write_text(text.replace(old, new))
                shutil.rmtree(target.parent / "__pycache__", ignore_errors=True)
                time.sleep(1.1)
                try:
                    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300, preexec_fn=cap,
                                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                    results.append((name, "KILLED" if done.returncode else "SURVIVED"))
                except subprocess.TimeoutExpired:
                    results.append((name, "CAPPED"))
                finally:
                    shutil.copy(pristine, target)
                    if sha(target) != digest:
                        raise SystemExit(f"could not restore {target}; copy at {pristine}")
        finally:
            shutil.copy(pristine, target)
        shutil.rmtree(target.parent / "__pycache__", ignore_errors=True)
        for name, outcome in results:
            print(f"{outcome:12} {name}")
        bad = [n for n, o in results if o != "KILLED"]
        print(f"{len(results) - len(bad)} of {len(results)} killed; restored: {sha(target) == digest}")
        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
