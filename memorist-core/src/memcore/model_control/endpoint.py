from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


class EndpointConfigurationError(ValueError):
    """Raised when a provider endpoint cannot be normalized safely."""


_OPERATION_SUFFIXES = (
    "/chat/completions",
    "/embeddings",
    "/models",
)


@dataclass(frozen=True)
class CanonicalProviderEndpoint:
    """Canonical OpenAI-compatible endpoint coordinates.

    ``base_url`` includes the API version prefix and never includes an operation
    path.  Keeping operation paths out of stored profiles prevents constructions
    such as ``/v1/chat/completions/v1/chat/completions``.
    """

    base_url: str
    origin_url: str
    api_version_prefix: str
    endpoint_family: str
    normalized_from_operation_url: bool

    def operation_url(self, operation: str) -> str:
        operation_name = operation.strip("/")
        if not operation_name:
            raise EndpointConfigurationError("provider operation must not be empty")
        return f"{self.base_url}/{operation_name}"


def normalize_openai_endpoint(endpoint_url: str) -> CanonicalProviderEndpoint:
    raw = endpoint_url.strip()
    if not raw:
        raise EndpointConfigurationError("endpoint_url is required")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EndpointConfigurationError(
            "endpoint_url must be an absolute http(s) URL without credentials"
        )
    if parsed.username or parsed.password:
        raise EndpointConfigurationError("endpoint_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise EndpointConfigurationError(
            "endpoint_url must not contain query parameters or fragments"
        )

    path = _clean_path(parsed.path)
    lowered_path = path.lower()
    operation_matches = [suffix for suffix in _OPERATION_SUFFIXES if suffix in lowered_path]
    if operation_matches and (
        len(operation_matches) != 1
        or lowered_path.count(operation_matches[0]) != 1
        or not lowered_path.endswith(operation_matches[0])
    ):
        raise EndpointConfigurationError(
            "endpoint_url contains a duplicated or non-terminal OpenAI operation path"
        )
    operation_removed = False
    for suffix in _OPERATION_SUFFIXES:
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)]
            operation_removed = True
            break

    # Only an origin (or an unversioned operation directly on the origin) gets
    # the conventional /v1 default. Any other non-empty path is an explicit
    # provider API base and must be preserved. Gateways commonly expose custom
    # bases such as /openai or /tenant/api; silently inserting /v1 into those
    # paths changes the provider contract.
    segments = [segment for segment in path.split("/") if segment]
    if segments and _is_version_segment(segments[-1]):
        version_prefix = f"/{segments[-1]}"
        base_path = "/" + "/".join(segments)
    elif segments:
        version_prefix = ""
        base_path = "/" + "/".join(segments)
    else:
        version_prefix = "/v1"
        base_path = version_prefix

    normalized = SplitResult(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=base_path,
        query="",
        fragment="",
    )
    origin = SplitResult(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path="",
        query="",
        fragment="",
    )
    return CanonicalProviderEndpoint(
        base_url=urlunsplit(normalized).rstrip("/"),
        origin_url=urlunsplit(origin).rstrip("/"),
        api_version_prefix=version_prefix,
        endpoint_family="openai_compatible",
        normalized_from_operation_url=operation_removed,
    )


def openai_operation_url(endpoint_url: str, operation: str) -> str:
    return normalize_openai_endpoint(endpoint_url).operation_url(operation)


def _clean_path(path: str) -> str:
    pieces = [piece for piece in path.replace("\\", "/").split("/") if piece]
    return "/" + "/".join(pieces) if pieces else ""


def _is_version_segment(segment: str) -> bool:
    lowered = segment.lower()
    return len(lowered) > 1 and lowered.startswith("v") and lowered[1:].isdigit()
