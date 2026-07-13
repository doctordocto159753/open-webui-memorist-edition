from collections.abc import Generator

import pytest

from memcore.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    if request.path.name not in {"test_config.py", "test_trusted_actor_authentication.py"}:
        monkeypatch.setenv("MEMORIST_ENV", "test")
        monkeypatch.setenv("MEMORIST_ALLOW_LEGACY_ACTOR_HEADERS_FOR_TESTS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
