from __future__ import annotations

import logging

from .errors import sanitize_error


def logger() -> logging.Logger:
    log = logging.getLogger("memorist.openwebui")
    if not log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s memorist.openwebui %(message)s"))
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    return log


def warn(message: str, error: BaseException | str | None = None) -> None:
    if error is None:
        logger().warning(message)
    else:
        logger().warning("%s: %s", message, sanitize_error(error))
