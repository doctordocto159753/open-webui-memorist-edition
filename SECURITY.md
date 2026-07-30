# Security and Privacy

Memorist `0.2.0-beta.3` is a beta development candidate of a **local-first**
memory layer. This
document describes the trust model, how secrets and personal data are handled,
what the system defends against, and — just as importantly — what it does not
guarantee.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through
**GitHub Security Advisories** ("Report a vulnerability" on the repository's
Security tab) rather than public issues. Include reproduction steps and
redact any real keys or personal data. As an alpha project maintained on a
best-effort basis, there is no guaranteed response SLA, but reports are
genuinely welcome and prioritized over feature work.

Supported status: only the latest `main` / most recent release is supported.

## Trust model

Memorist runs entirely on the user's machine: Open WebUI, `memorist-core`,
and the data stores are local containers, with service ports bound to
loopback. The trust boundary is **the local machine and its Docker
environment** — anyone with access to your user account, the Docker daemon,
or the disk can read the data. Memorist does not add encryption at rest and
does not claim to protect you from a compromised host.

- `MEMORIST_LOCAL_ONLY=true` is the default and setting it to `false` is
  rejected.
- No telemetry is sent.
- The only outbound traffic is image pulls and any remote model provider
  **you explicitly configure**.

## API keys and secrets

Two rules define the secret model:

1. **The browser and database never hold plaintext provider keys.** The Memory
   Setup UI stores an environment-variable *reference* (a name such as
   `MEMORIST_MEMORY_EXTRACTION_API_KEY`). The backend resolves the value from
   its own process environment. Model Control APIs never return raw keys;
   profile summaries report only whether a reference is configured; raw
   keys/tokens submitted in profile fields are rejected and redacted.
2. **Key values live only in the local `.env` / container environment.** The
   installer writes keys you enter into the git-ignored, ACL-restricted
   `.env`, masks them in all output (`****last4`), and never echoes or logs
   them. Session/actor secrets are generated with a cryptographically strong
   RNG.

Honest caveat: for this local desktop alpha, `.env` is **plaintext on your
disk**. That is a deliberate, documented trade-off — not weak "encryption"
that would only create false security. Keep the install folder private, and
treat `.env` like a password file. Never commit it (it is git-ignored, and the
release forbidden-file scanner blocks `.env` and key-like content from
entering packages).

## Remote provider privacy

Every memory-processing role has a **local deterministic fallback**, so a
fresh install works with no API key and no remote calls. If you configure a
remote OpenAI-compatible provider, role-specific data may leave your machine:

- *memory extraction* may send captured user/assistant text or sentence units;
- *embedding* may send memory or query text;
- *preflight* may send current user text and retrieval candidates;
- optional privacy/compaction/import roles may send derived summaries or
  imported fragments.

Before a non-local endpoint can become a role default, Memorist requires an
explicit **privacy acknowledgement** of the risk level and the data categories
involved (Settings → Memorist → Processing Nodes → Privacy). Memorist reduces
unnecessary remote exposure by supporting local/deterministic modes and
explicit provider configuration, **but users are responsible for any remote
provider they configure** — its retention, logging, and jurisdiction are
outside Memorist's control.

## Consent: Memory On / Memory Off

The per-chat memory toggle is enforced **server-side** as a consent ceiling:
a Memory Off turn creates no capture, no processing job, no retrieval, and no
attachment — a forged request cannot re-enable it, and regeneration honors
the state recorded on the original turn. The toggle governs new processing;
erasing existing memories is a separate privacy/forget workflow with
preview → confirm → execute semantics, quarantine, and residue checks.

## Untrusted-content defenses

Memory and imported text are treated as **data, not instruction**:

- Memory Context Attachments are rendered with escaped delimiters, provenance
  metadata, and instruction-like-content detection; the original user prompt
  is never modified.
- Prompt-like content, delimiter attacks, scope-expansion attempts, tool-call
  attempts, and secret requests inside memory are flagged and escaped rather
  than promoted to directives.
- Import archives pass staging validation (path traversal, symlinks/devices,
  nested archives, compression-bomb checks) before extraction, and imported
  content stays untrusted evidence.
- Assistant/tool/system-derived text carries distinct provenance and never
  silently gains user-level authority.

Prompt injection **cannot be eliminated** — these are mitigations, not a
guarantee.

## Data at rest and deletion limits

Conversations, memory candidates, canonical versions, retrieval traces,
attachments, and import staging live in the local SQLite/PostgreSQL database
and object store configured by `.env`. Deletion honesty:

- logical deletion and privacy erasure remove or quarantine data inside
  Memorist tables and invalidate derived indexes (verified by residue checks);
- SQLite `secure_delete` can improve deletion assurance at a performance cost;
- filesystems, SSD wear-leveling, and your own backups may retain physical
  remnants **outside Memorist's control**.

## Open WebUI integration boundary

Open WebUI Filters and Functions execute Python **server-side**. Install only
the trusted integration files shipped in this repository/release package.
Browser code talks only to the authenticated `/api/v1/memorist/*` router; the
backend ignores browser-supplied identity headers and signs short-lived,
purpose-bound assertions for `memorist-core`. Memory Setup operations require
a verified Open WebUI administrator.

## What is not guaranteed

- No protection against a compromised host, malicious local user, or hostile
  Docker environment.
- No encryption at rest; no key management service.
- No security audit has been performed; this is alpha software.
- No guarantee that any specific remote provider is safe to use.
- No elimination of prompt injection — only layered mitigation.
