Prompt ID: memorist.contradiction_detection
Prompt Version: 2.0
Allowed model role: memory_extraction

Classify relationships between a candidate and existing memory versions.
Correction is not the same as contradiction. Retraction requires strong evidence.
History must be preserved. Low confidence becomes MANUAL_REVIEW.

Allowed relation values: reinforces, updates, contradicts, corrects, retracts, unrelated, unclear.
Allowed recommended_action values: REINFORCE, UPDATE, SUPERSEDE, CONTRADICT, RETRACT, MANUAL_REVIEW, NOOP.
Return only the standard I-JSON envelope.

Input payload:
{{PAYLOAD_IJSON}}
