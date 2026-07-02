from memcore.imports.reconstruction.models import ImportedConversation


def branch_count(conversation: ImportedConversation) -> int:
    return sum(1 for message in conversation.messages.values() if len(message.child_ids) > 1)


def active_path(conversation: ImportedConversation) -> list[str]:
    if (
        conversation.active_leaf_id is None
        or conversation.active_leaf_id not in conversation.messages
    ):
        return []
    path: list[str] = []
    current = conversation.active_leaf_id
    while current:
        path.append(current)
        parents = conversation.messages[current].parent_ids
        current = parents[0] if parents else ""
    return list(reversed(path))
