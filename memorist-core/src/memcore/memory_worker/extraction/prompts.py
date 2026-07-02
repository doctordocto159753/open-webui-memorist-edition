import hashlib

EXTRACTION_PROMPT_VERSION = "candidate-extraction-v1"
EXTRACTION_PROMPT_TEMPLATE = """
Create candidate claims only when exact evidence spans support them. Never turn
assistant speculation, questions, hypotheticals, secrets or quoted third-party
claims into accepted user facts.
"""
EXTRACTION_PROMPT_HASH = hashlib.sha256(EXTRACTION_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()
