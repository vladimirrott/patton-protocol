"""Container side of the loopback link: listen on 127.0.0.1:<port> and pipe each connection to a unix socket.

    python3 -I fwd.py <port> <socket path> <ready file>

The container runs with --network=none, so its only way out is the unix socket the host mounted, and the host end
of that socket connects only to the scripted model. The harness is pointed at http://127.0.0.1:<port>/v1.
Creates <ready file> once listening, so the entrypoint can wait for it before starting the harness.
"""

import socket
import sys
import threading


def pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def serve(conn: socket.socket, path: str) -> None:
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(path)
    except OSError:
        conn.close()
        return
    threading.Thread(target=pump, args=(conn, up), daemon=True).start()
    pump(up, conn)


def main() -> None:
    port, path = int(sys.argv[1]), sys.argv[2]
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    open(sys.argv[3], "w").close()
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(conn, path), daemon=True).start()


if __name__ == "__main__":
    main()
