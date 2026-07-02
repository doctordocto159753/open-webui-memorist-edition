from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from memcore.models import ModelRole, new_uuid, utc_now
from memcore.validators.ijson import dump_ijson, load_ijson


@dataclass(frozen=True)
class ModelRegistryEntry:
    model_profile_uuid: str
    provider: str
    model_name: str
    model_aliases: list[str]
    context_window: int | None
    max_output_tokens_default: int | None
    tokenizer_family: str
    supports_token_counting: bool
    last_verified_at: str | None
    source: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_profile_uuid": self.model_profile_uuid,
            "provider": self.provider,
            "model_name": self.model_name,
            "model_aliases": self.model_aliases,
            "context_window": self.context_window,
            "max_output_tokens_default": self.max_output_tokens_default,
            "tokenizer_family": self.tokenizer_family,
            "supports_token_counting": self.supports_token_counting,
            "last_verified_at": self.last_verified_at,
            "source": self.source,
            "confidence": self.confidence,
        }


BUILTIN_MODELS: list[dict[str, Any]] = [
    {
        "provider": "openai-compatible",
        "model_name": "llama3",
        "model_aliases": ["llama3", "llama-3"],
        "context_window": 8192,
        "max_output_tokens_default": 1024,
        "tokenizer_family": "heuristic",
        "supports_token_counting": False,
        "source": "builtin",
        "confidence": 0.7,
    },
    {
        "provider": "openai-compatible",
        "model_name": "qwen2.5",
        "model_aliases": ["qwen2.5", "qwen-2.5"],
        "context_window": 32768,
        "max_output_tokens_default": 2048,
        "tokenizer_family": "heuristic",
        "supports_token_counting": False,
        "source": "builtin",
        "confidence": 0.7,
    },
]


class ModelRegistry:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_builtin_entries(self) -> None:
        for item in BUILTIN_MODELS:
            self.create_or_update(**item)

    def list_entries(self) -> list[dict[str, Any]]:
        self.ensure_builtin_entries()
        rows = self.connection.execute(
            """
            SELECT *
            FROM model_registry_entries
            ORDER BY provider, model_name
            """
        ).fetchall()
        return [_row_to_entry(row).as_dict() for row in rows]

    def resolve(self, model_name: str | None, provider: str | None = None) -> dict[str, Any] | None:
        if not model_name:
            return None
        self.ensure_builtin_entries()
        normalized = model_name.lower()
        rows = self.connection.execute("SELECT * FROM model_registry_entries").fetchall()
        for row in rows:
            entry = _row_to_entry(row)
            aliases = {entry.model_name.lower(), *(alias.lower() for alias in entry.model_aliases)}
            provider_matches = provider is None or entry.provider == provider
            if provider_matches and normalized in aliases:
                return entry.as_dict()
        return None

    def create_or_update(
        self,
        provider: str,
        model_name: str,
        model_aliases: list[str] | None = None,
        context_window: int | None = None,
        max_output_tokens_default: int | None = None,
        tokenizer_family: str = "heuristic",
        supports_token_counting: bool = False,
        last_verified_at: str | None = None,
        source: str = "user_configured",
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        existing = self.connection.execute(
            """
            SELECT *
            FROM model_registry_entries
            WHERE provider = ? AND model_name = ?
            """,
            (provider, model_name),
        ).fetchone()
        now = utc_now()
        aliases = model_aliases or [model_name]
        if existing is None:
            model_profile_uuid = new_uuid()
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO model_registry_entries (
                        model_profile_uuid, provider, model_name, model_aliases_ijson,
                        context_window, max_output_tokens_default, tokenizer_family,
                        supports_token_counting, last_verified_at, source, confidence,
                        created_at, updated_at, schema_version
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1)
                    """,
                    (
                        model_profile_uuid,
                        provider,
                        model_name,
                        dump_ijson(aliases),
                        context_window,
                        max_output_tokens_default,
                        tokenizer_family,
                        int(supports_token_counting),
                        last_verified_at,
                        source,
                        confidence,
                        now,
                    ),
                )
        else:
            model_profile_uuid = str(existing["model_profile_uuid"])
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE model_registry_entries
                    SET model_aliases_ijson = ?, context_window = ?,
                        max_output_tokens_default = ?, tokenizer_family = ?,
                        supports_token_counting = ?, last_verified_at = ?,
                        source = ?, confidence = ?, updated_at = ?
                    WHERE model_profile_uuid = ?
                    """,
                    (
                        dump_ijson(aliases),
                        context_window,
                        max_output_tokens_default,
                        tokenizer_family,
                        int(supports_token_counting),
                        last_verified_at,
                        source,
                        confidence,
                        now,
                        model_profile_uuid,
                    ),
                )
        return self.get(model_profile_uuid)

    def patch(self, model_profile_uuid: str, values: dict[str, Any]) -> dict[str, Any]:
        current = self.get(model_profile_uuid)
        merged = {**current, **values}
        return self.create_or_update(
            provider=str(merged["provider"]),
            model_name=str(merged["model_name"]),
            model_aliases=list(merged.get("model_aliases") or []),
            context_window=merged.get("context_window"),
            max_output_tokens_default=merged.get("max_output_tokens_default"),
            tokenizer_family=str(merged.get("tokenizer_family") or "heuristic"),
            supports_token_counting=bool(merged.get("supports_token_counting", False)),
            last_verified_at=merged.get("last_verified_at"),
            source=str(merged.get("source") or "user_configured"),
            confidence=float(merged.get("confidence") or 0.8),
        )

    def get(self, model_profile_uuid: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM model_registry_entries WHERE model_profile_uuid = ?",
            (model_profile_uuid,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Model registry entry not found: {model_profile_uuid}")
        return _row_to_entry(row).as_dict()


def model_profile_role() -> ModelRole:
    return ModelRole.MAIN_CHAT


def _row_to_entry(row: sqlite3.Row) -> ModelRegistryEntry:
    aliases = load_ijson(row["model_aliases_ijson"])
    if not isinstance(aliases, list):
        aliases = [row["model_name"]]
    return ModelRegistryEntry(
        model_profile_uuid=str(row["model_profile_uuid"]),
        provider=str(row["provider"]),
        model_name=str(row["model_name"]),
        model_aliases=[str(alias) for alias in aliases],
        context_window=row["context_window"],
        max_output_tokens_default=row["max_output_tokens_default"],
        tokenizer_family=str(row["tokenizer_family"]),
        supports_token_counting=bool(row["supports_token_counting"]),
        last_verified_at=row["last_verified_at"],
        source=str(row["source"]),
        confidence=float(row["confidence"]),
    )
