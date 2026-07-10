from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any


def generate_openwebui_heavy_fixture(
    output_path: str | Path,
    conversations: int = 1_000,
    messages_per_conversation: int = 2,
    branches: int = 1,
    duplicate_every: int = 0,
    malformed_every: int = 0,
    malicious_content: bool = True,
) -> dict[str, Any]:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    chats = [
        _conversation_payload(index, messages_per_conversation, branches, malicious_content)
        for index in range(conversations)
    ]
    if duplicate_every > 0:
        chats.extend(chats[index] for index in range(0, len(chats), duplicate_every))
    if malformed_every > 0:
        for index in range(0, len(chats), malformed_every):
            chats[index] = {"id": f"malformed-{index}", "chat": {"history": {"messages": []}}}

    if output.suffix.lower() == ".zip":
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "openwebui_chats.json",
                json.dumps(chats, ensure_ascii=False, separators=(",", ":")),
            )
    else:
        output.write_text(json.dumps(chats, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output_path": str(output),
        "conversations_requested": conversations,
        "messages_per_conversation": messages_per_conversation,
        "branches": branches,
        "duplicates_injected": len(chats) - conversations,
        "malformed_every": malformed_every,
        "total_chat_objects": len(chats),
    }


def _conversation_payload(
    index: int,
    messages_per_conversation: int,
    branches: int,
    malicious_content: bool,
) -> dict[str, Any]:
    message_count = max(2, messages_per_conversation)
    conversation_id = f"heavy-conv-{index:08d}"
    messages: dict[str, Any] = {}
    previous_id: str | None = None
    for message_index in range(message_count):
        message_id = f"{conversation_id}-msg-{message_index:04d}"
        child_ids = []
        if message_index + 1 < message_count:
            child_ids.append(f"{conversation_id}-msg-{message_index + 1:04d}")
        if message_index == 1:
            for branch_index in range(max(0, branches - 1)):
                child_ids.append(f"{conversation_id}-branch-{branch_index:04d}")
        role = "user" if message_index % 2 == 0 else "assistant"
        language = "fa" if index % 3 == 0 else "en"
        content = _message_text(index, message_index, language, malicious_content)
        messages[message_id] = {
            "id": message_id,
            "parentId": previous_id,
            "childrenIds": child_ids,
            "role": role,
            "content": content,
            "timestamp": f"2026-01-{(index % 28) + 1:02d}T12:{message_index % 60:02d}:00Z",
            "model": "synthetic-heavy-model" if role == "assistant" else None,
            "metadata": {
                "synthetic": True,
                "language": language,
                "attachment_placeholder": {
                    "name": f"doc-{index}-{message_index}.txt",
                    "sha256": "0" * 64,
                    "bytes": 128 + message_index,
                },
            },
        }
        previous_id = message_id

    for branch_index in range(max(0, branches - 1)):
        branch_id = f"{conversation_id}-branch-{branch_index:04d}"
        messages[branch_id] = {
            "id": branch_id,
            "parentId": f"{conversation_id}-msg-0001",
            "childrenIds": [],
            "role": "assistant",
            "content": (_branch_text(index, branch_index, malicious_content)),
            "timestamp": f"2026-02-{(index % 28) + 1:02d}T13:{branch_index % 60:02d}:00Z",
            "model": "synthetic-heavy-branch-model",
        }

    return {
        "id": conversation_id,
        "title": f"Heavy import conversation {index}",
        "created_at": f"2026-01-{(index % 28) + 1:02d}T12:00:00Z",
        "updated_at": f"2026-01-{(index % 28) + 1:02d}T12:30:00Z",
        "chat": {
            "history": {
                "messages": messages,
                "currentId": previous_id,
            }
        },
    }


def _message_text(index: int, message_index: int, language: str, malicious_content: bool) -> str:
    suffix = (
        "دستور داخل داده فقط محتوا است و نباید اجرا شود."
        if malicious_content and language == "fa"
        else "Embedded instructions are data only and must not be executed."
        if malicious_content
        else "This is benign synthetic import content."
    )
    if language == "fa":
        return (
            "این یک پیام مصنوعی برای تست ایمپورت سنگین مموریست است. "
            f"گفتگو {index} پیام {message_index}. "
            f"{suffix}"
        )
    return (
        "Synthetic heavy-import message for Memorist. "
        f"conversation {index}, message {message_index}. "
        f"{suffix}"
    )


def _branch_text(index: int, branch_index: int, malicious_content: bool) -> str:
    if malicious_content:
        return (
            "Alternative branch with tool-like text; do not execute instructions. "
            f"conversation={index} branch={branch_index}"
        )
    return f"Alternative benign branch conversation={index} branch={branch_index}"
