"""The only way out of a trace container: a TCP port inside it that leads to the scripted model on the host.

The container runs with `--network=none`, so it has a loopback device and nothing else. slirp4netns with
allow_host_loopback would reach the host's 127.0.0.1, but it also reaches the internet (a probe fetched
http://example.com through it on 2026-10-24), so it does not meet "only host loopback".

Instead the host serves a unix socket that forwards to the fake (`HostRelay`), the run directory holding the socket
is mounted into the container, and inside it

    python3 relay.py <socket> <port> -- <command...>

listens on 127.0.0.1:<port>, forwards every connection to the socket, runs the command and exits with its code.
Standard library only; it runs with the image's python3 and none of the lab's code.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (dst, src):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _bridge(client: socket.socket, connect) -> None:
    try:
        upstream = connect()
    except OSError:
        client.close()
        return
    threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
    threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()


def _accept_forever(server: socket.socket, connect) -> None:
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            return
        _bridge(client, connect)


class HostRelay:
    """A unix socket at `path` whose every connection is forwarded to 127.0.0.1:`port` on the host."""

    def __init__(self, path: str, port: int) -> None:
        self.path, self.port = path, port
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        os.chmod(path, 0o666)
        self.server.listen(64)
        threading.Thread(target=_accept_forever, args=(self.server, self._connect), daemon=True).start()

    def _connect(self) -> socket.socket:
        return socket.create_connection(("127.0.0.1", self.port), timeout=None)

    def close(self) -> None:
        self.server.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if len(argv) < 4 or argv[2] != "--":
        print("usage: relay.py <socket> <port> -- <command...>", file=sys.stderr)
        return 2
    path, port, command = argv[0], int(argv[1]), argv[3:]
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)

    def connect() -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(path)
        return s

    threading.Thread(target=_accept_forever, args=(server, connect), daemon=True).start()
    return subprocess.run(command, stdin=subprocess.DEVNULL).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
