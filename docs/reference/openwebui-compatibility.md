# Open WebUI Compatibility

Memorist integrates with Open WebUI through local server-side Filter and Function files under `open-webui-integration/memorist`. The beta compatibility target is pinned to:

`ghcr.io/open-webui/open-webui:v0.9.6`

The release gate is contract-based: it runs the Memorist payload parser, inlet/outlet lifecycle tests, fail-open tests, idempotency tests, fixture payloads, and model control plane tests. A full Open WebUI version matrix is not claimed for v0.2.0-beta.3.

## Compatibility Levels

- Automated: `make openwebui-contract-tests` runs the local Filter/Function contract tests.
- Semi-automated: `make openwebui-container-smoke` prints the pinned target and exits skipped unless container execution is requested directly.
- Manual: install the Filter/Function in a disposable Open WebUI account and verify the chat flow.

## Optional Container Smoke

Run the optional local container smoke from the repository root:

```powershell
cd memorist-core
uv run python ../release/tests/openwebui_container_smoke.py --run-containers
```

The script uses `docker-compose.openwebui-smoke.yml`, starts `memorist-core` and the pinned Open WebUI image, then verifies both local HTTP services respond. It does not claim that the Filter is installed into an Open WebUI account; that remains a manual beta check.

To test another image explicitly:

```powershell
cd memorist-core
uv run python ../release/tests/openwebui_container_smoke.py --run-containers --image ghcr.io/open-webui/open-webui:v0.9.6
```

## Manual Checklist

1. Start Memorist Core and Open WebUI locally.
2. In Open WebUI admin settings, install `open-webui-integration/memorist/filter/memorist_memory_filter.py`.
3. Install `open-webui-integration/memorist/function/memorist_status_function.py`.
4. Set the Filter valve `MEMORIST_CORE_URL` to `http://host.docker.internal:8777` when Open WebUI runs in Docker, or `http://127.0.0.1:8777` when both run on the host.
5. Create a new chat and send a user message.
6. Confirm the original user message is unchanged.
7. Confirm Memorist context appears only as a separate untrusted memory-context message when retrieval has usable content.
8. Confirm assistant capture does not duplicate on repeated outlet callbacks.
9. Stop Memorist Core and confirm Open WebUI fails open instead of blocking chat.

## Source of Truth

- Contract metadata: `open-webui-integration/compatibility/supported_versions.ijson`
- Contract details: `open-webui-integration/compatibility/openwebui_contract.md`
- Payload fixtures: `open-webui-integration/compatibility/payload_fixtures/`
