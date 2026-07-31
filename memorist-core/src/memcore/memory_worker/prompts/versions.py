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

SEMANTIC_CANDIDATE_ANALYSIS_PROMPT_ID = "memorist.semantic_candidate_analysis"
SEMANTIC_CANDIDATE_ANALYSIS_V1_VERSION = "1.0"
# v1.0 remains immutable for historical replay. v1.1 is the active
# message-first contract with canonical metadata and structural semantic units.
SEMANTIC_CANDIDATE_ANALYSIS_VERSION = "1.1"
SEMANTIC_CANDIDATE_ANALYSIS_STAGE = "semantic_candidate_analysis"

PREFLIGHT_PLANNING_PROMPT_ID = "memorist.preflight_planning"
PREFLIGHT_PLANNING_V2_VERSION = "2.0"
PREFLIGHT_PLANNING_VERSION = "2.1"

PROMPT_PACK_ID = "memorist-memory-worker-prompt-pack-v2"
PROMPT_PACK_VERSION = "2.0"
