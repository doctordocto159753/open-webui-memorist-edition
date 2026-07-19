from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

# The real-host acceptance test only runs where the pinned Open WebUI is
# installed (the pr5g backend-route job). Contract jobs that install only
# Memorist Core must not even collect it — a collected-but-skipped test would
# trip their "no skipped tests" false-green guard. Excluding it from
# collection here produces zero skips instead.
if importlib.util.find_spec("open_webui") is None:
    collect_ignore = ["test_real_openwebui_host.py"]


@pytest.fixture(autouse=True)
def isolate_synthetic_openwebui_host(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the isolated mount test independent from the real-host acceptance gate.

    ``test_shipped_entrypoint_mounts_router_with_verified_openwebui_user`` builds a
    deliberately synthetic FastAPI/Open WebUI module graph. It validates dependency
    binding and route mounting only; distribution-version detection and startup
    provisioning are exercised against the installed pinned host in
    ``test_real_openwebui_host.py``. Without this boundary, workflows that correctly
    install only Memorist Core fail before reaching the isolated assertions.
    """
    if request.node.name != "test_shipped_entrypoint_mounts_router_with_verified_openwebui_user":
        return

    from memorist.backend import openwebui_entrypoint

    def certified_version() -> str:
        return "0.9.6"

    def skip_real_host_lifespan(_app: object) -> None:
        return None

    monkeypatch.setattr(
        openwebui_entrypoint,
        "assert_supported_openwebui_version",
        certified_version,
    )
    monkeypatch.setattr(
        openwebui_entrypoint,
        "_install_memorist_lifespan",
        skip_real_host_lifespan,
    )
