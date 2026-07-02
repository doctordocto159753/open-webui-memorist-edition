# Model Costs

Model usage is tracked per role, provider, model, stage, and day. Local deterministic defaults report zero provider billing; this does not mean compute is free, only that there is no provider invoice.

Diagnostics:

```sh
curl http://localhost:8777/memcore/model-control/usage
curl http://localhost:8777/memcore/costs/model-roles
```

Cost profiles can include:

- `input_token_cost` or `input_per_1k`
- `output_token_cost` or `output_per_1k`
- `embedding_unit_cost` or `embedding_per_1k`
- `currency`
- `estimated_daily_cost`
- `soft_warning_limit`
- `hard_daily_limit`

Latency and quality profiles are exposed honestly. Unknown quality remains `unknown`; Memorist does not invent eval scores.
