from __future__ import annotations

import base64
import hmac
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from memcore.config import Settings, get_settings
from memcore.memory_control import memory_control_connection
from memcore.models import utc_now

_actor_context: ContextVar[AuthenticatedMemoristActor | None] = ContextVar(
    "memorist_actor", default=None
)


@dataclass(frozen=True)
class AuthenticatedMemoristActor:
    user_uuid: str
    workspace_uuid: str
    issuer: str
    audience: str
    purpose: str
    nonce: str


PROTECTED_PATHS = (
    "/memcore/memory-control",
    "/memcore/retrieval/",
    "/memcore/attachments/",
    "/memcore/preflight",
    "/memcore/assistant-response/completed",
    "/memcore/openwebui/session/resolve",
    "/memcore/openwebui/messages/capture",
    "/memcore/openwebui/outcomes",
    "/memcore/openwebui/sessions/",
    "/memcore/openwebui/status",
)


class MemoristActorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not any(request.url.path.startswith(prefix) for prefix in PROTECTED_PATHS):
            return await call_next(request)
        settings = get_settings()
        if settings.allow_legacy_actor_headers_for_tests and not request.headers.get(
            "X-Memorist-User-Id"
        ):
            return await call_next(request)
        try:
            actor = authenticate_request(request, settings)
        except HTTPException as error:
            return Response(
                json.dumps({"detail": error.detail}),
                status_code=error.status_code,
                media_type="application/json",
            )
        token = _actor_context.set(actor)
        try:
            return await call_next(request)
        finally:
            _actor_context.reset(token)


def current_memorist_actor() -> AuthenticatedMemoristActor:
    actor = _actor_context.get()
    if actor is None:
        raise HTTPException(status_code=401, detail="authenticated Memorist actor required")
    return actor


def optional_memorist_actor() -> AuthenticatedMemoristActor | None:
    return _actor_context.get()


def authenticate_request(request: Request, settings: Settings) -> AuthenticatedMemoristActor:
    if settings.allow_legacy_actor_headers_for_tests:
        user = request.headers.get("X-Memorist-User-Id")
        workspace = request.headers.get("X-Memorist-Workspace-Id")
        if user and workspace:
            return AuthenticatedMemoristActor(
                user, workspace, "test-only", "memorist-core", "test", "test-only"
            )
    service = request.headers.get("X-Memorist-Service-Token")
    if not settings.actor_service_token or not service:
        raise HTTPException(status_code=401, detail="Memorist service credential required")
    if not hmac.compare_digest(service, settings.actor_service_token):
        raise HTTPException(status_code=401, detail="invalid Memorist service credential")
    assertion = request.headers.get("X-Memorist-Actor-Assertion")
    if not assertion or not settings.actor_assertion_secret:
        raise HTTPException(status_code=401, detail="signed actor assertion required")
    actor = verify_actor_assertion(
        assertion,
        settings,
        purpose=f"{request.method}:{request.url.path}",
    )
    _consume_nonce(actor, settings)
    return actor


def verify_actor_assertion(
    token: str, settings: Settings, *, purpose: str, now: int | None = None
) -> AuthenticatedMemoristActor:
    secret = settings.actor_assertion_secret
    if not secret:
        raise HTTPException(status_code=503, detail="actor assertion verifier is not configured")
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), encoded.encode(), sha256).digest())
        if not hmac.compare_digest(supplied_signature, expected):
            raise ValueError("signature")
        claims = json.loads(_b64decode(encoded))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=401, detail="invalid actor assertion") from error
    current = int(time.time()) if now is None else now
    required = {"sub", "workspace_uuid", "iss", "aud", "iat", "exp", "purpose", "nonce"}
    if not required.issubset(claims):
        raise HTTPException(status_code=401, detail="actor assertion claims incomplete")
    if claims["iss"] != settings.actor_assertion_issuer:
        raise HTTPException(status_code=401, detail="actor assertion issuer rejected")
    if claims["aud"] != settings.actor_assertion_audience:
        raise HTTPException(status_code=401, detail="actor assertion audience rejected")
    if claims["purpose"] != purpose:
        raise HTTPException(status_code=403, detail="actor assertion scope rejected")
    if int(claims["iat"]) > current + 5 or int(claims["exp"]) < current:
        raise HTTPException(status_code=401, detail="actor assertion expired")
    if int(claims["exp"]) - int(claims["iat"]) > settings.actor_assertion_max_ttl_seconds:
        raise HTTPException(status_code=401, detail="actor assertion lifetime rejected")
    if not claims["sub"] or not claims["workspace_uuid"] or not claims["nonce"]:
        raise HTTPException(status_code=401, detail="actor assertion identity incomplete")
    return AuthenticatedMemoristActor(
        str(claims["sub"]),
        str(claims["workspace_uuid"]),
        str(claims["iss"]),
        str(claims["aud"]),
        str(claims["purpose"]),
        str(claims["nonce"]),
    )


def _consume_nonce(actor: AuthenticatedMemoristActor, settings: Settings) -> None:
    try:
        with memory_control_connection(settings) as connection:
            connection.execute(
                "DELETE FROM memorist_actor_assertion_nonces WHERE expires_at < ?",
                (int(time.time()),),
            )
            connection.execute(
                "INSERT INTO memorist_actor_assertion_nonces "
                "(nonce, user_uuid, workspace_uuid, purpose, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    actor.nonce,
                    actor.user_uuid,
                    actor.workspace_uuid,
                    actor.purpose,
                    int(time.time()) + settings.actor_assertion_max_ttl_seconds,
                    utc_now(),
                ),
            )
            connection.commit()
    except Exception as error:
        raise HTTPException(status_code=409, detail="actor assertion replay rejected") from error


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
