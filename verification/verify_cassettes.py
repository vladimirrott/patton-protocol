#!/usr/bin/env python3
"""Tamper-evidence check for verification/cassettes/.

Every cassette directory (verification/cassettes/<harness>/<mission-id>/<run>/)
holds a run_metadata.md and a manifest.sha256 in standard `sha256sum` format
covering every other file in that directory. `log.md` records the sha256 of
each manifest. This script either:

- writes/updates a directory's manifest.sha256 (`--write <dir>`), or
- checks the whole tree (default, no args) and exits non-zero naming every
  problem: an artifact that changed, a cassette whose manifest is gone, a
  manifest whose digest `log.md` never recorded, or a tree with no cassettes
  at all.

A cassette is found by its run_metadata.md, not by its manifest. Finding it by
the manifest would let a deleted manifest remove the cassette from the count
and pass. An empty tree is a failure for the same reason: nothing was checked.

Dependency-free (stdlib only), matching the rest of this repo's tooling.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

MANIFEST_NAME = "manifest.sha256"
TRIAL_MARKER = "run_metadata.md"
LOG_NAME = "log.md"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cassette_files(cassette_dir: Path) -> list[Path]:
    return sorted(
        p
        for p in cassette_dir.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    )


def write_manifest(cassette_dir: Path) -> Path:
    manifest_path = cassette_dir / MANIFEST_NAME
    lines = []
    for path in cassette_files(cassette_dir):
        rel = path.relative_to(cassette_dir).as_posix()
        lines.append(f"{sha256_of(path)}  {rel}")
    manifest_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return manifest_path


def verify_manifest(cassette_dir: Path) -> list[str]:
    """Return a list of human-readable problems; empty means clean."""
    manifest_path = cassette_dir / MANIFEST_NAME
    problems: list[str] = []
    if not manifest_path.exists():
        return [f"{cassette_dir}: missing {MANIFEST_NAME}"]

    recorded: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        recorded[rel] = digest

    actual_files = {
        p.relative_to(cassette_dir).as_posix() for p in cassette_files(cassette_dir)
    }

    for rel, digest in recorded.items():
        full = cassette_dir / rel
        if not full.exists():
            problems.append(f"{cassette_dir}: {rel} listed in manifest but missing on disk")
            continue
        actual = sha256_of(full)
        if actual != digest:
            problems.append(
                f"{cassette_dir}: {rel} hash mismatch "
                f"(manifest {digest[:12]}... != disk {actual[:12]}...)"
            )

    for rel in actual_files - recorded.keys():
        problems.append(f"{cassette_dir}: {rel} present on disk but not in manifest")

    return problems


def find_trial_dirs(root: Path) -> list[Path]:
    """A trial is a directory that owns a run_metadata.md.

    The manifest is deliberately not the marker: a trial that lost its manifest
    must still be found so the missing manifest is reported.
    """
    cassettes_root = root / "cassettes"
    if not cassettes_root.exists():
        return []
    return sorted(p.parent for p in cassettes_root.rglob(TRIAL_MARKER))


def check(root: Path) -> list[str]:
    """Return every problem found under `root`; empty means every check ran and passed."""
    trials = find_trial_dirs(root)
    if not trials:
        return [f"{root}: no cassettes found, so nothing was checked"]

    log_path = root / LOG_NAME
    if not log_path.exists():
        return [f"{root}: {LOG_NAME} is missing, so no manifest digest can be confirmed"]
    log_text = log_path.read_text(encoding="utf-8")

    problems: list[str] = []
    for trial in trials:
        problems.extend(verify_manifest(trial))
        manifest_path = trial / MANIFEST_NAME
        if manifest_path.exists() and sha256_of(manifest_path) not in log_text:
            problems.append(
                f"{trial}: manifest digest {sha256_of(manifest_path)[:12]}... "
                f"not recorded in {LOG_NAME}"
            )
    return problems


def main(argv: list[str], root: Path | None = None) -> int:
    verification_root = root if root is not None else Path(__file__).resolve().parent

    if len(argv) >= 2 and argv[0] == "--write":
        target = Path(argv[1]).resolve()
        manifest_path = write_manifest(target)
        print(f"wrote {manifest_path}")
        return 0

    problems = check(verification_root)
    if problems:
        print(f"CASSETTE CHECK FAILED: {len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    n = len(find_trial_dirs(verification_root))
    print(f"OK: {n} cassette(s) verified, manifests match disk and every digest is in {LOG_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
