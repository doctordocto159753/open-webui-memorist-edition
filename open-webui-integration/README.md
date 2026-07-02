# Open WebUI integration

This folder contains the trusted server-side Memorist integration bundle for Open WebUI.

- `memorist/filter/memorist_memory_filter.py`: fail-open preflight Filter.
- `memorist/function/memorist_status_function.py`: sanitized status Function.
- `memorist/shared/`: local-only client/config helpers.
- `memorist/ui/`: minimum UI surface, Model Control Plane contract, and TypeScript client contract.
- `compatibility/`: payload fixtures, supported-version metadata, and Filter contract.

Open WebUI remains the parent application. Memorist adds local memory context as a separate untrusted message and never rewrites the original user prompt. Model roles are configured in Memorist: `main_chat_observed` is only observed from Open WebUI, while `preflight`, `memory_extraction`, `privacy_sensitivity`, `block_compaction`, `import_reconstruction`, and `embedding` are separate local-first roles.

The optional local container-smoke target is pinned to `ghcr.io/open-webui/open-webui:v0.9.6`. Automated beta release evidence is contract-fixture based unless the operator explicitly runs the container smoke.

Security warning: Open WebUI Filters and Functions execute Python on the server. Install only from a trusted local release.
