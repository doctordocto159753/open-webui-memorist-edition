from pathlib import Path

import pytest

from memcore.validators.ijson import (
    IJsonValidationError,
    canonical_hash_ijson,
    dump_ijson,
    load_ijson,
    main,
    validate_ijson_text,
    validate_ijson_value,
)
from memcore.validators.payload_policy import PayloadPolicyError, prepare_ijson_field


def test_valid_ijson_object_passes() -> None:
    validate_ijson_value(
        {
            "event_type": "message.created",
            "created_at": "2026-06-25T20:00:00Z",
            "metadata": {"source": "test"},
        }
    )


def test_duplicate_keys_fail() -> None:
    with pytest.raises(IJsonValidationError, match="Duplicate object member name: value"):
        validate_ijson_text('{"value":1,"value":2}')


def test_unsafe_large_integer_fails() -> None:
    with pytest.raises(IJsonValidationError, match="outside JavaScript safe range"):
        validate_ijson_value({"value": 9_007_199_254_740_992})


def test_large_integer_encoded_as_string_passes() -> None:
    validate_ijson_value({"value": "9007199254740992"})


def test_nan_fails() -> None:
    with pytest.raises(IJsonValidationError, match="NaN"):
        validate_ijson_text('{"value": NaN}')


def test_infinity_fails() -> None:
    with pytest.raises(IJsonValidationError, match="Infinity"):
        validate_ijson_text('{"value": Infinity}')


def test_unpaired_surrogate_fails() -> None:
    with pytest.raises(IJsonValidationError, match="unpaired surrogate"):
        validate_ijson_text('{"value":"\\ud800"}')


def test_rfc3339_timestamp_passes() -> None:
    validate_ijson_value({"valid_from": "2026-06-25T20:00:00+03:30"})


def test_bad_at_timestamp_fails() -> None:
    with pytest.raises(IJsonValidationError, match="RFC3339 timestamp"):
        validate_ijson_value({"created_at": "2026-06-25 20:00:00"})


def test_top_level_array_fails_for_event_payload_policy() -> None:
    with pytest.raises(PayloadPolicyError, match="payload_ijson must contain a top-level"):
        prepare_ijson_field("payload_ijson", '[{"event_type":"message.created"}]')


def test_top_level_array_can_pass_for_uuid_list_policy() -> None:
    prepared = prepare_ijson_field("source_memory_uuids_ijson", '["mem-1","mem-2"]')

    assert prepared == '["mem-1","mem-2"]'


def test_deterministic_dump_produces_stable_hash() -> None:
    first_value = {"b": 2, "a": {"d": 4, "c": 3}}
    second_value = {"a": {"c": 3, "d": 4}, "b": 2}

    assert dump_ijson(first_value) == '{"a":{"c":3,"d":4},"b":2}'
    assert dump_ijson(first_value) == dump_ijson(second_value)
    assert canonical_hash_ijson(first_value) == canonical_hash_ijson(second_value)


def test_load_ijson_preserves_unknown_fields() -> None:
    loaded = load_ijson('{"known":true,"future_field":{"nested":1}}')

    assert loaded == {"known": True, "future_field": {"nested": 1}}


def test_cli_reports_valid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_path = tmp_path / "payload.json"
    json_path.write_text('{"created_at":"2026-06-25T20:00:00Z"}', encoding="utf-8")

    exit_code = main([str(json_path)])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "VALID I-JSON"


def test_cli_reports_invalid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_path = tmp_path / "payload.json"
    json_path.write_text('{"created_at":"not-a-timestamp"}', encoding="utf-8")

    exit_code = main([str(json_path)])

    assert exit_code == 1
    assert capsys.readouterr().out.startswith("INVALID I-JSON:")
