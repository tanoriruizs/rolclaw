"""Healthcheck por socket Unix (el contenedor no tiene red). Uso: python -m sandbox_runner.health"""

from __future__ import annotations

import http.client
import os
import socket
import sys

DEFAULT_SOCKET = "/run/sandbox/sandbox.sock"


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 3.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def check(path: str) -> bool:
    connection = UnixHTTPConnection(path)
    try:
        connection.request("GET", "/health")
        return connection.getresponse().status == 200
    except OSError:
        return False
    finally:
        connection.close()


def main() -> int:
    return 0 if check(os.environ.get("SANDBOX_SOCKET", DEFAULT_SOCKET)) else 1


if __name__ == "__main__":
    sys.exit(main())
