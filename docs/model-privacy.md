# Model Privacy

Memorist defaults are local-safe: deterministic preflight, deterministic extraction, and disabled embeddings in Lite.

Remote providers can receive role-specific data:

- Preflight may receive current user text, retrieval candidates, active constraints, and budget metadata.
- Memory extraction may receive captured user/assistant text or sentence units.
- Embedding providers may receive memory text or query text.
- Optional privacy/block/import roles may receive derived memory summaries or historical import fragments.

The privacy matrix is exposed in the admin UI at:

```text
Settings → Memorist → Processing Nodes → Privacy
```

Before a non-local endpoint can become a default, open the remote processing node, review the Model Control privacy disclosure, acknowledge the remote risk level, and confirm the role-specific data categories that may be sent. For `memory_extraction`, that may include captured user/assistant text or sentence units.

Developer-only API fallback:

```sh
curl http://localhost:8777/memcore/model-control/privacy
curl -X POST http://localhost:8777/memcore/model-control/privacy/acknowledge \
  -H "Content-Type: application/json" \
  -d '{"model_profile_uuid":"...","acknowledged_risk_level":"external","acknowledged_data_sent":{"sends_raw_user_text":true}}'
```

Memorist never stores raw API keys in SQLite or PostgreSQL. Store provider secrets in the environment and put only the env-var name in the profile.
