from memcore.imports.models import ImportIssue
from memcore.imports.reconstruction.models import ImportedConversation


def validate_conversation(conversation: ImportedConversation) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    for message_id, message in conversation.messages.items():
        for parent_id in message.parent_ids:
            if parent_id not in conversation.messages:
                issues.append(
                    ImportIssue(
                        severity="warning",
                        issue_code="missing_parent",
                        message="Referenced parent message is missing",
                        source_record_id=message_id,
                        details={"parent_id": parent_id},
                    )
                )
        if _has_cycle(conversation, message_id):
            issues.append(
                ImportIssue(
                    severity="error",
                    issue_code="message_cycle",
                    message="Message graph contains a cycle",
                    source_record_id=message_id,
                )
            )
    if conversation.active_leaf_id and conversation.active_leaf_id not in conversation.messages:
        issues.append(
            ImportIssue(
                severity="warning",
                issue_code="missing_active_leaf",
                message="Active leaf is missing from message graph",
                source_record_id=conversation.active_leaf_id,
            )
        )
    return issues


def _has_cycle(conversation: ImportedConversation, start: str) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            return True
        seen.add(current)
        stack.extend(conversation.messages[current].parent_ids)
    return False
