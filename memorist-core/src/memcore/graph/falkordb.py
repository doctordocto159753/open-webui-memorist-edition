from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class FalkorDBHealth:
    ok: bool
    status: str
    error_sanitized: str | None = None


class FalkorDBClient:
    def __init__(self, url: str, graph_name: str = "memorist") -> None:
        self.url = url
        self.graph_name = graph_name

    def health(self, timeout_seconds: float = 1.0) -> FalkorDBHealth:
        try:
            host, port = self._host_port()
            with socket.create_connection((host, port), timeout=timeout_seconds) as connection:
                connection.sendall(b"*1\r\n$4\r\nPING\r\n")
                response = connection.recv(64)
            if response.startswith(b"+PONG"):
                return FalkorDBHealth(ok=True, status="ok")
            return FalkorDBHealth(ok=False, status="unexpected_response")
        except Exception as error:
            return FalkorDBHealth(ok=False, status="degraded", error_sanitized=str(error)[:240])

    def graph_query(self, query: str) -> bytes:
        host, port = self._host_port()
        command = self._resp_command(["GRAPH.QUERY", self.graph_name, query])
        with socket.create_connection((host, port), timeout=5.0) as connection:
            connection.sendall(command)
            response = connection.recv(4096)
        if response.startswith(b"-"):
            raise RuntimeError(response.decode("utf-8", errors="replace")[:240])
        return response

    def delete_graph(self) -> None:
        host, port = self._host_port()
        command = self._resp_command(["GRAPH.DELETE", self.graph_name])
        with socket.create_connection((host, port), timeout=5.0) as connection:
            connection.sendall(command)
            response = connection.recv(4096)
        if response.startswith(b"-") and b"does not exist" not in response:
            raise RuntimeError(response.decode("utf-8", errors="replace")[:240])

    def _host_port(self) -> tuple[str, int]:
        parsed = urlparse(self.url)
        return parsed.hostname or "localhost", parsed.port or 6379

    def _resp_command(self, parts: list[str]) -> bytes:
        encoded = [part.encode("utf-8") for part in parts]
        chunks = [f"*{len(encoded)}\r\n".encode("ascii")]
        for part in encoded:
            chunks.append(f"${len(part)}\r\n".encode("ascii"))
            chunks.append(part + b"\r\n")
        return b"".join(chunks)
