import sqlite3
from typing import Any

from memcore.active_memory.policies import source_is_eligible
from memcore.active_memory.schemas import BlockSource
from memcore.models import MemoryBlock, MemoryBlockType
from memcore.validators.ijson import load_ijson


def select_canonical_sources(
    connection: sqlite3.Connection,
    block: MemoryBlock,
) -> list[BlockSource]:
    if block.block_type is MemoryBlockType.CURRENT_SESSION_STATE:
        return []
    rows = connection.execute(
        f"""
        SELECT m.memory_uuid,
               m.canonical_key,
               m.memory_type,
               m.scope_type,
               m.scope_uuid,
               m.current_version_uuid,
               m.sensitivity_class,
               mv.memory_version_uuid,
               mv.normalized_text,
               mv.confidence,
               mv.valid_from,
               mv.valid_until,
               mv.change_reason_ijson,
               mc.source_authority
        FROM memories m
        JOIN memory_versions mv ON mv.memory_version_uuid = m.current_version_uuid
        LEFT JOIN memory_candidates mc ON mc.candidate_uuid = mv.source_candidate_uuid
        WHERE m.status = 'active'
          AND {scope_clause_for_block(block)}
        ORDER BY m.memory_type, m.canonical_key, mv.created_at
        """,
        scope_params_for_block(block),
    ).fetchall()
    sources = [_source_from_row(row) for row in rows]
    return [source for source in sources if source_is_eligible(block, source)]


def session_hot_cache_items(
    connection: sqlite3.Connection, block: MemoryBlock
) -> list[dict[str, Any]]:
    if block.block_type is not MemoryBlockType.CURRENT_SESSION_STATE or block.scope_uuid is None:
        return []
    row = connection.execute(
        "SELECT * FROM session_hot_cache WHERE session_uuid = ?",
        (block.scope_uuid,),
    ).fetchone()
    if row is None:
        return []
    items: list[dict[str, Any]] = []
    for key in ("current_problem", "last_user_intent", "current_plan"):
        if row[key]:
            items.append({"label": key, "text": row[key]})
    for key in ("active_constraints_ijson", "unresolved_questions_ijson"):
        if row[key]:
            value = load_ijson(row[key])
            if isinstance(value, list):
                items.extend({"label": key, "text": str(item)} for item in value)
    return items


def scope_clause_for_block(block: MemoryBlock) -> str:
    if block.scope_type == "project":
        return "m.scope_type = 'project' AND m.scope_uuid = ?"
    if block.scope_type == "workspace":
        return "m.scope_type = 'workspace' AND m.scope_uuid = ?"
    if block.scope_type == "session":
        return "m.scope_type = 'session' AND m.scope_uuid = ?"
    return "m.scope_type = ? AND COALESCE(m.scope_uuid, '') = COALESCE(?, '')"


def scope_params_for_block(block: MemoryBlock) -> tuple[Any, ...]:
    if block.scope_type in {"project", "workspace", "session"}:
        return (block.scope_uuid,)
    return (block.scope_type, block.scope_uuid)


def _source_from_row(row: sqlite3.Row) -> BlockSource:
    return BlockSource(
        memory_uuid=row["memory_uuid"],
        memory_version_uuid=row["memory_version_uuid"],
        canonical_key=row["canonical_key"],
        memory_type=row["memory_type"],
        scope_type=row["scope_type"],
        scope_uuid=row["scope_uuid"],
        normalized_text=row["normalized_text"],
        source_authority=row["source_authority"] or "unknown",
        current=row["current_version_uuid"] == row["memory_version_uuid"],
        confidence=float(row["confidence"]),
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        sensitivity_class=row["sensitivity_class"],
        user_confirmed=_is_user_confirmed(row["change_reason_ijson"]),
    )


def _is_user_confirmed(change_reason_ijson: str | None) -> bool:
    if not change_reason_ijson:
        return False
    try:
        value = load_ijson(change_reason_ijson)
    except ValueError:
        return False
    return bool(isinstance(value, dict) and value.get("user_confirmed") is True)
