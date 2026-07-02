from datetime import UTC, datetime


def normalize_timestamp(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return (
            datetime.fromtimestamp(float(value), UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    text = str(value)
    try:
        return (
            datetime.fromisoformat(text.replace("Z", "+00:00"))
            .astimezone(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except ValueError:
        return None
