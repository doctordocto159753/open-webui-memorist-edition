"""Apply the reviewed Memorist backend compatibility fix to Open WebUI 0.9.6.

The derivative image is pinned to an immutable upstream image digest.  Keep
this patch fail-closed as well: if the exact upstream file changes, refuse to
rewrite it instead of applying a best-effort textual edit.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

EXPECTED_SHA256 = "636d5aa53907733def4999677f1720d5d0a900934c67d3e88f115584d2ba9db8"
OLD = """    if not system_contents:
        return other_messages

    merged = {'role': 'system', 'content': '\\n'.join(system_contents)}
"""
NEW = """    if not system_contents:
        return other_messages

    # A single system message does not need consolidation. Preserve the
    # complete OpenAI message shape, including a semantic ``name`` such as
    # Memorist's ``memorist_context`` data boundary.
    if len(system_contents) == 1:
        return [
            next(
                message
                for message in messages
                if message.get('role') == 'system' and get_content_from_message(message)
            ),
            *other_messages,
        ]

    merged = {'role': 'system', 'content': '\\n'.join(system_contents)}
"""


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_openwebui_backend.py <open_webui/utils/misc.py>")

    target = Path(sys.argv[1])
    source = target.read_bytes()
    actual_sha256 = hashlib.sha256(source).hexdigest()
    if actual_sha256 != EXPECTED_SHA256:
        raise SystemExit(f"refusing to patch unexpected Open WebUI backend: {actual_sha256}")

    text = source.decode("utf-8")
    if text.count(OLD) != 1:
        raise SystemExit("expected merge_system_messages fragment exactly once")

    target.write_text(text.replace(OLD, NEW), encoding="utf-8", newline="\n")
    print(f"patched named system-message preservation in {target}")


if __name__ == "__main__":
    main()
