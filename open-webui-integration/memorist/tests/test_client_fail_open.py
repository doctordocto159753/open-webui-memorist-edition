from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.client import MemoristClient
from shared.config import MemoristIntegrationConfig
from shared.errors import UnsafeMemoristUrl, sanitize_error


def test_fail_open_defaults_true() -> None:
    config = MemoristIntegrationConfig()
    assert config.fail_open is True
    assert config.preflight_enabled is True


def test_client_rejects_remote_url() -> None:
    try:
        MemoristClient(MemoristIntegrationConfig(core_url="https://example.com"))
    except UnsafeMemoristUrl:
        return
    raise AssertionError("remote URL accepted")


def test_error_sanitizer_redacts_secret_like_text() -> None:
    assert sanitize_error("token=" + "REDACTED_TEST_VALUE") == "[redacted]"
