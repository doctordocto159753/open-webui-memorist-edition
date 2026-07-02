Prompt ID: memorist.block_compaction
Prompt Version: 2.0
Allowed model roles: block_compaction, memory_extraction fallback

Compact approved memories into an active memory block without inventing content.
The block is a derived view. Source memories are truth.
Do not summarize previous_block as source. Do not hide conflicts.
Preserve source_memory_uuids and conflict_memory_uuids. Stay under token_budget.

Return only the standard I-JSON envelope. Each item must include block_type, block_text, source_memory_uuids, conflict_memory_uuids, uncertainty_notes, token_estimate, coverage.

Input payload:
{{PAYLOAD_IJSON}}
