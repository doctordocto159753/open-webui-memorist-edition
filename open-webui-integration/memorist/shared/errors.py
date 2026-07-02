from __future__ import annotations


class MemoristIntegrationError(RuntimeError):
    def __init__(self, message: str, developer_visible: bool = False) -> None:
        super().__init__(message)
        self.developer_visible = developer_visible


class MemoristCoreUnavailable(MemoristIntegrationError):
    pass


class UnsafeMemoristUrl(MemoristIntegrationError):
    pass


def sanitize_error(error: BaseException | str) -> str:
    text = str(error)
    lowered = text.lower()
    if any(part in lowered for part in ("key", "token", "secret", "password", "credential")):
        return "[redacted]"
    return text.replace("\n", " ")[:180]
