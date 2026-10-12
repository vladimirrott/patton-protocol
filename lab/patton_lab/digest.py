"""The candidate content digest from references/safety-and-budgets.md.

Each entry is (relative POSIX path, executable flag 0 or 1, exact bytes). For
every entry, sorted by lossless path token, the digest input gets:

    token NUL flag NUL byte-length NUL bytes LF

and `content_digest` is `sha256:` plus the lowercase hex SHA-256 of that stream.
The empty snapshot hashes the empty stream.
"""

from __future__ import annotations

import hashlib

_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def lossless_path_token(path: str) -> str:
    out = []
    for byte in path.encode("utf-8"):
        if byte == 0x2F:
            out.append("/")
        elif byte in _UNRESERVED:
            out.append(chr(byte))
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def content_digest(entries: list[tuple[str, int, bytes]]) -> str:
    keyed = []
    seen: set[str] = set()
    for path, executable, data in entries:
        if executable not in (0, 1):
            raise ValueError(f"executable flag must be 0 or 1, got {executable!r} for {path}")
        token = lossless_path_token(path)
        if token in seen:
            raise ValueError(f"duplicate path token {token}; the spec requires rejection")
        seen.add(token)
        keyed.append((token, executable, data))
    keyed.sort(key=lambda item: item[0])
    stream = bytearray()
    for token, executable, data in keyed:
        stream += token.encode("ascii") + b"\x00"
        stream += str(executable).encode("ascii") + b"\x00"
        stream += str(len(data)).encode("ascii") + b"\x00"
        stream += data + b"\n"
    return "sha256:" + hashlib.sha256(bytes(stream)).hexdigest()
