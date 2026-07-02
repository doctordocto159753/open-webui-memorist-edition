import hashlib

ANALYSIS_PROMPT_VERSION = "analysis-v1"
ANALYSIS_PROMPT_TEMPLATE = """
Return concise structured classifications only. Do not include chain-of-thought,
hidden reasoning, scratchpads, or provider diagnostics outside the schema.
"""
ANALYSIS_PROMPT_HASH = hashlib.sha256(ANALYSIS_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
