from memcore.api.routes_health import get_health


def test_health_endpoint() -> None:
    assert get_health() == {
        "status": "ok",
        "service": "memorist-core",
        "local_only": True,
        "runtime_profile": "lite",
        "canonical_store": "sqlite",
        "graph_backend": "disabled",
        "graph_status": "disabled",
        "hot_scheduler": "disabled",
        "scheduler": "disabled",
        "full_mode_certification": "not_run",
        "profile_warnings": [],
    }
