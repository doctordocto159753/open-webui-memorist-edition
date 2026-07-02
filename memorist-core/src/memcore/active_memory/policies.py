from memcore.active_memory.schemas import BlockSource
from memcore.models import MemoryBlock, MemoryBlockType

TRUSTED_ACTORS = {"admin", "system", "trusted_user"}


def actor_can_build_block(block: MemoryBlock, actor_type: str) -> bool:
    if block.block_type is MemoryBlockType.SAFETY_PRIVACY:
        return actor_type in TRUSTED_ACTORS
    if block.read_only:
        return actor_type in TRUSTED_ACTORS
    return True


def source_is_eligible(block: MemoryBlock, source: BlockSource) -> bool:
    if not source.current:
        return False
    if source.sensitivity_class != "normal":
        return False
    if source.source_authority == "assistant_claim":
        return False
    if block.block_type is MemoryBlockType.USER_PROFILE:
        return source.memory_type in {"identity", "preference"}
    if block.block_type is MemoryBlockType.PROJECT_CONTEXT:
        return source.memory_type in {
            "decision",
            "constraint",
            "definition",
            "unresolved_question",
            "fact",
        }
    if block.block_type is MemoryBlockType.STYLE_POLICY:
        return (
            source.memory_type == "preference"
            and source.source_authority == "user_explicit"
            and any(term in source.normalized_text.lower() for term in {"style", "answer", "reply"})
        )
    if block.block_type is MemoryBlockType.PROMPT_RULES:
        return source.memory_type == "constraint" and source.source_authority in {
            "user_explicit",
            "system_instruction",
        }
    if block.block_type is MemoryBlockType.SAFETY_PRIVACY:
        return (
            source.memory_type == "constraint" and source.source_authority == "system_instruction"
        )
    if block.block_type is MemoryBlockType.CURRENT_SESSION_STATE:
        return source.scope_type == "session"
    return False


def source_role_for(block: MemoryBlock, source: BlockSource) -> str:
    if source.memory_type == "constraint":
        return "constraint"
    if block.block_type is MemoryBlockType.USER_PROFILE:
        return "profile_item"
    if block.block_type is MemoryBlockType.CURRENT_SESSION_STATE:
        return "session_state"
    return "context_item"
