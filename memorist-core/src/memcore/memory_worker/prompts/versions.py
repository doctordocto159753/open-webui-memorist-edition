JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID = "memorist.jakobson_sentence_analysis"
# Version 2.0 is retained, immutable, and used only to replay historical prompt
# executions. Version 3.0 is the canonical contract-first schema used for all new
# executions: ``items`` is the single canonical collection (the redundant
# ``sentences`` array is removed) and each item is a complete sentence
# annotation. See memcore.memory_worker.prompts.contracts.
JAKOBSON_SENTENCE_ANALYSIS_VERSION = "2.0"
JAKOBSON_SENTENCE_ANALYSIS_V3_VERSION = "3.0"
JAKOBSON_SENTENCE_ANALYSIS_ACTIVE_VERSION = JAKOBSON_SENTENCE_ANALYSIS_V3_VERSION
JAKOBSON_SENTENCE_ANALYSIS_STAGE = "jakobson_sentence_analysis"

PROMPT_PACK_ID = "memorist-memory-worker-prompt-pack-v2"
PROMPT_PACK_VERSION = "2.0"
