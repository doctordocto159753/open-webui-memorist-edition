# Model Privacy

Memorist defaults are local-safe: deterministic preflight, deterministic extraction, and disabled embeddings in Lite.

Remote providers can receive role-specific data:

- Preflight may receive current user text, retrieval candidates, active constraints, and budget metadata.
- Memory extraction may receive captured user/assistant text or sentence units.
- Embedding providers may receive memory text or query text.
- Optional privacy/block/import roles may receive derived memory summaries or historical import fragments.

The privacy matrix is available at:

```sh
curl http://localhost:8777/memcore/model-control/privacy
```

Before a non-local endpoint can become a default, call:

```sh
curl -X POST http://localhost:8777/memcore/model-control/privacy/acknowledge \
  -H "Content-Type: application/json" \
  -d '{"model_profile_uuid":"...","acknowledged_risk_level":"external","acknowledged_data_sent":{"sends_raw_user_text":true}}'
```

Memorist never stores raw API keys in SQLite or PostgreSQL. Store provider secrets in the environment and put only the env-var name in the profile.
