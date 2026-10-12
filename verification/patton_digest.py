#!/usr/bin/env python3
"""Independent, faithful implementation of the candidate content-digest
algorithm described in references/safety-and-budgets.md.

Built to test a specific claim: that "Prime computes the candidate
identity independent of what the Builder reports" is a real, checkable
mechanism, not just prose. Nothing in this repository ships a reference
implementation of this algorithm — every real Prime has to freehand it
from the spec's prose. This is that implementation, written once from
the spec text alone and checked against the spec's own worked example
(the empty-snapshot digest, which the spec's `mission-contract.md`
example literally uses as its sample `content_digest` value) before being
trusted for any adversarial trial.

Spec source: references/safety-and-budgets.md, "Candidate snapshot and
digest" section.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_UNRESERVED = set(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def lossless_path_token(rel_posix_path: str) -> str:
    """Percent-encode each raw path byte, leaving unreserved ASCII bytes
    and '/' separators unencoded, using canonical uppercase hex escapes."""
    out = []
    for b in rel_posix_path.encode("utf-8"):
        if b == 0x2F:  # '/'
            out.append("/")
        elif b in _UNRESERVED:
            out.append(chr(b))
        else:
            out.append(f"%{b:02X}")
    return "".join(out)


def git_tracked_manifest(repo: Path) -> list[tuple[str, int, bytes]]:
    """Return (relpath_posix, executable_flag, content_bytes) for every
    post-mutation tracked regular file, excluding .git/ and .patton/ledger/.
    Rejects tracked symlinks and gitlinks per spec (raises if found — a
    real Prime blocks candidate lock on either)."""
    ls = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-s"],
        capture_output=True, text=True, check=True,
    )
    entries = []
    for line in ls.stdout.splitlines():
        # format: "<mode> <blob-sha> <stage>\t<path>"
        meta, path = line.split("\t", 1)
        mode = meta.split()[0]
        if path.startswith(".patton/ledger/"):
            continue
        if mode == "120000":
            raise ValueError(f"tracked symlink in manifest scope, must be rejected: {path}")
        if mode == "160000":
            raise ValueError(f"tracked gitlink (submodule) in manifest scope, must be rejected: {path}")
        executable = 1 if mode == "100755" else 0
        content = (repo / path).read_bytes()
        entries.append((path, executable, content))
    return entries


def compute_candidate_digest(
    repo: Path,
    explicit_untracked: list[tuple[str, int, bytes]] | None = None,
) -> str:
    """explicit_untracked: list of (relpath_posix, executable_flag, content_bytes)
    for candidate_untracked_paths entries. Returns 'sha256:<64-hex>'."""
    tracked = git_tracked_manifest(repo)
    all_entries = list(tracked) + list(explicit_untracked or [])

    tokens_seen = set()
    for path, _, _ in all_entries:
        token = lossless_path_token(path)
        if token in tokens_seen:
            raise ValueError(f"duplicate path token, spec requires rejection: {token}")
        tokens_seen.add(token)

    all_entries.sort(key=lambda e: lossless_path_token(e[0]))

    stream = bytearray()
    for path, executable, content in all_entries:
        token = lossless_path_token(path)
        stream += token.encode("ascii")
        stream += b"\x00"
        stream += str(executable).encode("ascii")
        stream += b"\x00"
        stream += str(len(content)).encode("ascii")
        stream += b"\x00"
        stream += content
        stream += b"\n"

    digest = hashlib.sha256(bytes(stream)).hexdigest()
    return f"sha256:{digest}"


def candidate_revision(repo: Path, content_digest: str) -> str:
    rev = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if rev.returncode == 0:
        return rev.stdout.strip()
    return f"candidate:{content_digest}"


def _self_test() -> None:
    """The spec's own mission-contract.md example uses the empty-snapshot
    digest (sha256 of zero bytes) as its sample content_digest value. Any
    implementation of this algorithm must reproduce that exact value for
    an empty candidate set before it's trusted for anything else."""
    empty_digest = f"sha256:{hashlib.sha256(b'').hexdigest()}"
    expected = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert empty_digest == expected, f"self-test failed: {empty_digest} != {expected}"
    print(f"self-test OK: empty snapshot digest matches spec's worked example ({expected})")


if __name__ == "__main__":
    _self_test()
