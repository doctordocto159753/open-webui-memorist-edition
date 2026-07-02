from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from memcore.models import Session, new_uuid, utc_now
from memcore.repositories import SessionRepository
from memcore.storage.sqlite import with_busy_retry

AliasType = Literal[
    "openwebui_chat_id",
    "openwebui_temp_chat_id",
    "client_session_nonce",
    "first_message_fingerprint",
    "conversation_fingerprint",
]


@dataclass(frozen=True)
class SessionResolutionInput:
    workspace_uuid: str | None
    project_uuid: str | None
    openwebui_conversation_id: str | None = None
    temporary_chat_id: str | None = None
    client_session_nonce: str | None = None
    first_message_hash: str | None = None
    user_id: str | None = None
    title: str | None = None
    source_app: str = "open_webui"
    created_at: str | None = None


@dataclass(frozen=True)
class SessionResolutionResult:
    session: Session
    matched_alias_type: str | None
    aliases: list[dict[str, object]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def resolve_session(
    connection: sqlite3.Connection,
    request: SessionResolutionInput,
) -> SessionResolutionResult:
    return with_busy_retry(lambda: _resolve_session_once(connection, request))


def list_aliases(connection: sqlite3.Connection, session_uuid: str) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT alias_uuid, session_uuid, alias_type, alias_value, user_id, conversation_id,
               first_message_hash, client_session_nonce, confidence, status, created_at,
               last_seen_at, schema_version
        FROM openwebui_session_aliases
        WHERE session_uuid = ?
        ORDER BY created_at, alias_type
        """,
        (session_uuid,),
    ).fetchall()
    return [dict(row) for row in rows]


def first_message_fingerprint(
    user_id: str | None,
    first_message_hash: str,
    created_at: str | None,
    source_app: str,
) -> str:
    material = "|".join(
        [
            _normalize(user_id) or "anonymous",
            _normalize(first_message_hash) or "",
            _date_bucket(created_at),
            _normalize(source_app) or "open_webui",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _resolve_session_once(
    connection: sqlite3.Connection,
    request: SessionResolutionInput,
) -> SessionResolutionResult:
    with connection:
        return _resolve_session_in_transaction(connection, request)


def _resolve_session_in_transaction(
    connection: sqlite3.Connection,
    request: SessionResolutionInput,
) -> SessionResolutionResult:
    diagnostics: list[str] = []
    aliases = _candidate_aliases(request)
    for alias_type, alias_value in aliases:
        session, diagnostic = _session_for_alias(
            connection,
            alias_type,
            alias_value,
            request.user_id,
            request.workspace_uuid,
        )
        if diagnostic:
            diagnostics.append(diagnostic)
        if session is not None:
            _attach_aliases(connection, session.session_uuid, request, aliases, diagnostics)
            _update_stable_conversation_id(connection, session.session_uuid, request)
            return SessionResolutionResult(
                session=session,
                matched_alias_type=alias_type,
                aliases=list_aliases(connection, session.session_uuid),
                diagnostics=diagnostics,
            )

    session = _legacy_session_by_conversation_id(connection, request)
    matched_alias_type: str | None = None
    if session is None:
        session = SessionRepository(connection).create_session(
            workspace_uuid=request.workspace_uuid,
            project_uuid=request.project_uuid,
            openwebui_conversation_id=_normalize(request.openwebui_conversation_id),
            title=request.title,
        )
        diagnostics.append("created_session_without_existing_alias")
    else:
        matched_alias_type = "legacy_openwebui_conversation_id"

    _attach_aliases(connection, session.session_uuid, request, aliases, diagnostics)
    return SessionResolutionResult(
        session=session,
        matched_alias_type=matched_alias_type,
        aliases=list_aliases(connection, session.session_uuid),
        diagnostics=diagnostics,
    )


def _candidate_aliases(request: SessionResolutionInput) -> list[tuple[AliasType, str]]:
    aliases: list[tuple[AliasType, str]] = []
    if _normalize(request.openwebui_conversation_id):
        aliases.append(("openwebui_chat_id", _normalize(request.openwebui_conversation_id) or ""))
    if _normalize(request.client_session_nonce):
        aliases.append(("client_session_nonce", _normalize(request.client_session_nonce) or ""))
    if _normalize(request.temporary_chat_id):
        aliases.append(("openwebui_temp_chat_id", _normalize(request.temporary_chat_id) or ""))
    if _normalize(request.first_message_hash):
        aliases.append(
            (
                "first_message_fingerprint",
                first_message_fingerprint(
                    request.user_id,
                    _normalize(request.first_message_hash) or "",
                    request.created_at,
                    request.source_app,
                ),
            )
        )
    return aliases


def _session_for_alias(
    connection: sqlite3.Connection,
    alias_type: str,
    alias_value: str,
    user_id: str | None,
    workspace_uuid: str | None,
) -> tuple[Session | None, str | None]:
    row = connection.execute(
        """
        SELECT s.*
        FROM openwebui_session_aliases AS a
        JOIN sessions AS s ON s.session_uuid = a.session_uuid
        WHERE a.alias_type = ?
          AND a.alias_value = ?
          AND COALESCE(a.user_id, '') = COALESCE(?, '')
          AND a.status = 'active'
        ORDER BY a.confidence DESC, a.last_seen_at DESC
        LIMIT 2
        """,
        (alias_type, alias_value, _normalize(user_id)),
    ).fetchall()
    if not row:
        return None, None
    if len(row) > 1:
        return None, f"ambiguous_alias:{alias_type}"
    session = Session.model_validate(dict(row[0]))
    if workspace_uuid and session.workspace_uuid and session.workspace_uuid != workspace_uuid:
        return None, f"alias_workspace_conflict:{alias_type}"
    now = utc_now()
    connection.execute(
        """
        UPDATE openwebui_session_aliases
        SET last_seen_at = ?
        WHERE alias_type = ?
          AND alias_value = ?
          AND COALESCE(user_id, '') = COALESCE(?, '')
        """,
        (now, alias_type, alias_value, _normalize(user_id)),
    )
    return session, None


def _legacy_session_by_conversation_id(
    connection: sqlite3.Connection,
    request: SessionResolutionInput,
) -> Session | None:
    conversation_id = _normalize(request.openwebui_conversation_id)
    if not conversation_id or request.user_id:
        return None
    row = connection.execute(
        """
        SELECT *
        FROM sessions
        WHERE openwebui_conversation_id = ?
          AND (? IS NULL OR workspace_uuid IS NULL OR workspace_uuid = ?)
        ORDER BY created_at
        LIMIT 1
        """,
        (conversation_id, request.workspace_uuid, request.workspace_uuid),
    ).fetchone()
    return Session.model_validate(dict(row)) if row else None


def _attach_aliases(
    connection: sqlite3.Connection,
    session_uuid: str,
    request: SessionResolutionInput,
    aliases: list[tuple[AliasType, str]],
    diagnostics: list[str],
) -> None:
    now = utc_now()
    for alias_type, alias_value in aliases:
        connection.execute(
            """
            INSERT OR IGNORE INTO openwebui_session_aliases (
                alias_uuid, session_uuid, alias_type, alias_value, user_id, conversation_id,
                first_message_hash, client_session_nonce, confidence, status, created_at,
                last_seen_at, schema_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1)
            """,
            (
                new_uuid(),
                session_uuid,
                alias_type,
                alias_value,
                _normalize(request.user_id),
                _normalize(request.openwebui_conversation_id),
                _normalize(request.first_message_hash),
                _normalize(request.client_session_nonce),
                _confidence(alias_type),
                now,
                now,
            ),
        )
        row = connection.execute(
            """
            SELECT session_uuid
            FROM openwebui_session_aliases
            WHERE alias_type = ?
              AND alias_value = ?
              AND COALESCE(user_id, '') = COALESCE(?, '')
            """,
            (alias_type, alias_value, _normalize(request.user_id)),
        ).fetchone()
        if row is not None and str(row["session_uuid"]) != session_uuid:
            diagnostics.append(f"alias_session_conflict:{alias_type}")
            continue
        connection.execute(
            """
            UPDATE openwebui_session_aliases
            SET last_seen_at = ?,
                conversation_id = COALESCE(?, conversation_id),
                client_session_nonce = COALESCE(?, client_session_nonce)
            WHERE alias_type = ?
              AND alias_value = ?
              AND COALESCE(user_id, '') = COALESCE(?, '')
            """,
            (
                now,
                _normalize(request.openwebui_conversation_id),
                _normalize(request.client_session_nonce),
                alias_type,
                alias_value,
                _normalize(request.user_id),
            ),
        )


def _update_stable_conversation_id(
    connection: sqlite3.Connection,
    session_uuid: str,
    request: SessionResolutionInput,
) -> None:
    conversation_id = _normalize(request.openwebui_conversation_id)
    if not conversation_id:
        return
    connection.execute(
        """
        UPDATE sessions
        SET openwebui_conversation_id = ?,
            updated_at = ?
        WHERE session_uuid = ?
          AND (openwebui_conversation_id IS NULL OR openwebui_conversation_id != ?)
        """,
        (conversation_id, utc_now(), session_uuid, conversation_id),
    )


def _confidence(alias_type: str) -> float:
    if alias_type == "openwebui_chat_id":
        return 1.0
    if alias_type == "client_session_nonce":
        return 0.95
    if alias_type == "openwebui_temp_chat_id":
        return 0.85
    return 0.75


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _date_bucket(value: str | None) -> str:
    text = _normalize(value) or utc_now()
    if len(text) >= 13 and text[4:5] == "-" and text[7:8] == "-":
        return text[:13]
    return utc_now()[:13]
