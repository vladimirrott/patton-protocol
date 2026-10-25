"""git inside the lab's harness containers, which the pinned python image (Debian 13) does not ship.

The fleet's P1 runs make /work a git repository and may have the gate run checks at a stop, so `git` must be on
PATH in the container. The image has no git and the runs have no network to install it, so git 2.47.3 is copied
once from the local `docker.io/library/rust:1-trixie` image (also Debian 13: the same libc, libpcre2-8 and libz) into
lab/harness_cache/git-trixie/ and bind-mounted read-only:

    podman create docker.io/library/rust:1-trixie true      # then podman cp of the three paths below, podman rm

`mounts()` gives the podman `-v` pairs, or raises when the copy is missing, so a run never starts without it.
"""

from __future__ import annotations

from pathlib import Path

CACHE = Path(__file__).resolve().parents[2] / "harness_cache" / "git-trixie"
GIT_SHA256 = "356db14e102d68a1a37d8a1ac577dfd678d45d46e92f468bef8b7154e7bfdc60"


def why_not() -> str | None:
    import hashlib
    if not (CACHE / "git").is_file() or not (CACHE / "git-core").is_dir():
        return "no git copy in lab/harness_cache/git-trixie (see patton_lab/harness/gitmount.py)"
    if hashlib.sha256((CACHE / "git").read_bytes()).hexdigest() != GIT_SHA256:
        return "lab/harness_cache/git-trixie/git is not the pinned build"
    return None


def mounts() -> list[tuple[Path, str]]:
    why = why_not()
    if why:
        raise RuntimeError(why)
    return [(CACHE / "git", "/usr/local/bin/git:ro"), (CACHE / "git-core", "/usr/lib/git-core:ro"),
            (CACHE / "share-git-core", "/usr/share/git-core:ro")]
