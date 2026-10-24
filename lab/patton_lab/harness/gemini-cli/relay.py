"""A TCP <-> Unix-socket relay, so a container with `--network none` can reach the scripted model and nothing else.

Host side:      UnixRelay(sock_path, tcp_port).start()   # the socket file forwards to 127.0.0.1:tcp_port
Container side: python3 relay.py listen <port> <sock>     # 127.0.0.1:<port> forwards to the socket file

The container gets the socket's directory as a mount and no network device but loopback, so the harness can
open http://127.0.0.1:<port> and every other address fails. Standard library only.
"""

from __future__ import annotations

import os
import socket
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
        for s, how in ((dst, socket.SHUT_WR), (src, socket.SHUT_RD)):
            try:
                s.shutdown(how)
            except OSError:
                pass


def _join(a: socket.socket, b: socket.socket) -> None:
    t = threading.Thread(target=_pump, args=(b, a), daemon=True)
    t.start()
    _pump(a, b)
    t.join(timeout=60)
    a.close()
    b.close()


class UnixRelay:
    def __init__(self, sock_path: str, tcp_port: int) -> None:
        self.sock_path, self.tcp_port = sock_path, tcp_port
        self._server: socket.socket | None = None

    def start(self) -> None:
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.sock_path)
        os.chmod(self.sock_path, 0o666)
        self._server.listen(64)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while self._server is not None:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            try:
                upstream = socket.create_connection(("127.0.0.1", self.tcp_port), timeout=30)
                upstream.settimeout(None)
            except OSError:
                conn.close()
                continue
            threading.Thread(target=_join, args=(conn, upstream), daemon=True).start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)


def listen(port: int, sock_path: str) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    print("relay ready", flush=True)
    while True:
        conn, _ = server.accept()
        try:
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.connect(sock_path)
        except OSError:
            conn.close()
            continue
        threading.Thread(target=_join, args=(conn, upstream), daemon=True).start()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "listen":
        sys.exit("usage: relay.py listen <port> <sock>")
    listen(int(sys.argv[2]), sys.argv[3])
