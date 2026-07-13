from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Any, BinaryIO
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

    def graph_query_parsed(self, query: str) -> Any:
        host, port = self._host_port()
        command = self._resp_command(["GRAPH.QUERY", self.graph_name, query])
        with socket.create_connection((host, port), timeout=5.0) as connection:
            connection.sendall(command)
            with connection.makefile("rb") as stream:
                response = _read_resp(stream)
        if isinstance(response, RuntimeError):
            raise response
        return response

    def query_memory_versions(
        self,
        *,
        workspace_uuid: str,
        terms: list[str],
        limit: int = 10,
    ) -> list[dict[str, str]]:
        normalized_terms = [term.strip().lower() for term in terms if term.strip()][:8]
        if not normalized_terms:
            return []
        term_list = ", ".join(_cypher_quote(term) for term in normalized_terms)
        query = "\n".join(
            [
                f"MATCH (w:Workspace {{uuid: {_cypher_quote(workspace_uuid)}}})",
                "MATCH (w)-[:IN_PROJECT]->(:Project)-[:HAS_SESSION]->(:Session)-[:HAS_MESSAGE]->"
                "(:Message)-[:HAS_UNIT]->(:TextUnit)-[:HAS_JAKOBSON_ANNOTATION]->"
                "(:JakobsonAnnotation)-[:ROUTES_TO]->(:MemorySignalRoute)-[:DERIVED_FROM]->"
                "(:MemoryCandidate)<-[:EVIDENCED_BY]-(ver:MemoryVersion)",
                "MATCH (mem:Memory)-[:HAS_VERSION]->(ver)",
                "WHERE mem.status = 'active' AND ver.status = 'current'",
                f"AND any(term IN [{term_list}] WHERE "
                "toLower(coalesce(ver.text, '')) CONTAINS term)",
                "RETURN DISTINCT mem.uuid AS memory_uuid, ver.uuid AS memory_version_uuid",
                f"LIMIT {max(1, min(limit, 50))}",
            ]
        )
        result = self.graph_query_parsed(query)
        return _graph_rows(result)

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


def _read_resp(stream: BinaryIO) -> Any:
    prefix = stream.read(1)
    if not prefix:
        raise RuntimeError("empty FalkorDB response")
    line = stream.readline().rstrip(b"\r\n")
    if prefix == b"+":
        return line.decode("utf-8", errors="replace")
    if prefix == b"-":
        return RuntimeError(line.decode("utf-8", errors="replace")[:240])
    if prefix == b":":
        return int(line)
    if prefix == b"$":
        length = int(line)
        if length < 0:
            return None
        value = stream.read(length)
        stream.read(2)
        return value.decode("utf-8", errors="replace")
    if prefix == b"*":
        length = int(line)
        if length < 0:
            return None
        return [_read_resp(stream) for _ in range(length)]
    raise RuntimeError("unsupported FalkorDB RESP value")


def _graph_rows(result: Any) -> list[dict[str, str]]:
    if not isinstance(result, list) or len(result) < 2:
        return []
    headers, rows = result[0], result[1]
    if not isinstance(headers, list) or not isinstance(rows, list):
        return []
    names = []
    for header in headers:
        if isinstance(header, list) and header:
            names.append(str(header[-1]))
        else:
            names.append(str(header))
    output: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        values = [str(item[-1] if isinstance(item, list) and item else item) for item in row]
        record = dict(zip(names, values, strict=False))
        memory_uuid = record.get("memory_uuid") or record.get("mem.uuid")
        version_uuid = record.get("memory_version_uuid") or record.get("ver.uuid")
        if memory_uuid and version_uuid:
            output.append({"memory_uuid": memory_uuid, "memory_version_uuid": version_uuid})
    return output


def _cypher_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
