import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

MAX_SAFE_INTEGER = 9_007_199_254_740_991
MIN_SAFE_INTEGER = -MAX_SAFE_INTEGER

TIMESTAMP_SUFFIXES = ("_at", "_from", "_until")
RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class IJsonValidationError(ValueError):
    """Raised when a value is not compatible with the Memorist I-JSON profile."""


def validate_ijson_text(text: str) -> None:
    load_ijson(text)


def validate_ijson_value(value: Any) -> None:
    _validate_value(value, "$", None)


def dump_ijson(value: Any) -> str:
    validate_ijson_value(value)
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def load_ijson(text: str) -> Any:
    _validate_string(text, "$")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except IJsonValidationError:
        raise
    except json.JSONDecodeError as error:
        raise IJsonValidationError(f"Invalid JSON text: {error.msg}") from error

    validate_ijson_value(value)
    return value


def canonical_hash_ijson(value: Any) -> str:
    canonical_text = dump_ijson(value)
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("INVALID I-JSON: expected exactly one JSON file path")
        return 2

    path = Path(args[0])
    try:
        validate_ijson_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, IJsonValidationError) as error:
        print(f"INVALID I-JSON: {error}")
        return 1

    print("VALID I-JSON")
    return 0


def _object_pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IJsonValidationError(f"Duplicate object member name: {key}")
        result[key] = value
    return result


def _reject_json_constant(constant: str) -> NoReturn:
    raise IJsonValidationError(f"Non-finite number is not valid I-JSON: {constant}")


def _validate_value(value: Any, path: str, field_name: str | None) -> None:
    if isinstance(value, bytes | bytearray | memoryview):
        raise IJsonValidationError(
            f"{path} contains binary data; use a base64url string with explicit metadata"
        )
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _validate_string(value, path)
        if field_name is not None and _is_timestamp_field(field_name):
            _validate_timestamp(value, path)
        return
    if isinstance(value, int):
        if value < MIN_SAFE_INTEGER or value > MAX_SAFE_INTEGER:
            raise IJsonValidationError(
                f"{path} contains integer outside JavaScript safe range; encode it as a string"
            )
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IJsonValidationError(f"{path} contains non-finite float")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_value(item, f"{path}[{index}]", None)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise IJsonValidationError(f"{path} contains a non-string object member name")
            _validate_string(key, f"{path}.{key}")
            child_path = f"{path}.{key}"
            if _is_timestamp_field(key) and not isinstance(item, str):
                raise IJsonValidationError(f"{child_path} must be an RFC3339 timestamp string")
            _validate_value(item, child_path, key)
        return

    value_type = type(value).__name__
    raise IJsonValidationError(f"{path} contains unsupported JSON value type: {value_type}")


def _validate_string(value: str, path: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise IJsonValidationError(f"{path} contains an unpaired surrogate character") from error


def _is_timestamp_field(field_name: str) -> bool:
    return field_name.endswith(TIMESTAMP_SUFFIXES)


def _validate_timestamp(value: str, path: str) -> None:
    if RFC3339_TIMESTAMP.fullmatch(value) is None:
        raise IJsonValidationError(f"{path} must be an RFC3339 timestamp string")

    normalized_value = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized_value)
    except ValueError as error:
        raise IJsonValidationError(f"{path} must be a valid RFC3339 timestamp") from error


if __name__ == "__main__":
    raise SystemExit(main())
