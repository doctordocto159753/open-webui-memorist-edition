from __future__ import annotations

from memcore.security.adversarial_cases import cases
from memcore.security.attachment_safety import attachment_safety_report


def test_adversarial_fixtures() -> None:
    for case in cases():
        memories = [
            {
                "memory_key": case.case_id,
                "text": case.content,
                "source": "import",
                "scope": "project-a",
            }
        ]
        report = attachment_safety_report(memories, allowed_scope="project-a")
        rendered = str(report["rendered_attachment"])
        assert "</memory_context_attachment>" not in rendered
        assert report["trusted_directive_count"] == 0
        assert report["untrusted_memory_count"] == 1


def test_scope_and_secret_exclusion() -> None:
    report = attachment_safety_report(
        [
            {"memory_key": "ok", "text": "normal", "scope": "project-a"},
            {"memory_key": "leak", "text": "other", "scope": "project-b"},
            {
                "memory_key": "secret",
                "text": "token=" + "REDACTED_TEST_VALUE",
                "scope": "project-a",
            },
        ],
        allowed_scope="project-a",
    )
    rendered = str(report["rendered_attachment"])
    assert "other" not in rendered
    assert "REDACTED_TEST_VALUE" not in rendered
