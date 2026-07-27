"""Deterministic local OpenAI-compatible model stub for PR5-G browser E2E.

No billable provider is involved. The stub answers /v1/models and
/v1/chat/completions (streaming and non-streaming) with deterministic text,
and records the *safe structure* of every request to REQUEST_LOG_PATH as JSON
lines so tests can assert that the Memorist attachment reached the model as
data. Authorization header values are never persisted — only their presence.
"""

from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "memorist-e2e-stub"
LOG_PATH = os.environ.get("REQUEST_LOG_PATH", "/logs/requests.jsonl")
_LOG_LOCK = threading.Lock()

_SECRETISH = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|[A-Za-z0-9_\-]{32,})")


def _redact(text: str) -> str:
    return _SECRETISH.sub("[redacted]", text)


def _record(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with _LOG_LOCK:
        with open(LOG_PATH, "a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _deterministic_answer(messages: list[dict]) -> str:
    """Answer from provided context only; deterministic and offline."""
    context = " ".join(
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "system"
    )
    question = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                question = " ".join(
                    str(part.get("text") or "") for part in content if isinstance(part, dict)
                )
            else:
                question = str(content or "")
            break
    if "memorist_provider_test" in question:
        return json.dumps({"memorist_provider_test": "ok"})
    try:
        provider_test = json.loads(question)
    except (json.JSONDecodeError, TypeError):
        provider_test = None
    if isinstance(provider_test, dict) and provider_test.get("memorist_provider_test") == "ok":
        return json.dumps({"memorist_provider_test": "ok"})
    if isinstance(provider_test, dict) and isinstance(provider_test.get("sentences"), list):
        return json.dumps(_jakobson_output(provider_test))
    lowered = (question + " " + context).lower()
    if "alpha" in lowered and "chicken" in lowered:
        return "Your dog is named Alpha and her preferred food is chicken."
    if "dog" in lowered:
        return "I do not have a remembered answer about your dog in this context."
    return f"Deterministic stub reply to: {question[:120]}"


def _jakobson_output(input_payload: dict) -> dict:
    items = []
    for index, item in enumerate(input_payload.get("sentences") or [], start=1):
        text = str(item.get("text") or "")
        lowered = text.lower()
        preference = "prefer" in lowered
        dominant = (
            "emotive"
            if preference
            else ("conative" if text.rstrip().endswith("?") else "referential")
        )
        context = "prefer" if preference else text
        factor = lambda value, evidence: {  # noqa: E731 - compact deterministic fixture
            "value": value,
            "evidence": evidence,
            "confidence": "high",
        }
        items.append(
            {
                "id": int(item.get("id") or index),
                "text": text,
                "six_factors": {
                    "sender_addresser": factor("user", text),
                    "receiver_addressee": factor("assistant", text),
                    "message": factor(text, text),
                    "context_referent": factor(context, text),
                    "code": factor("English", text),
                    "contact_channel": factor("chat", text),
                },
                "dominant_function": dominant,
                "secondary_functions": ["referential"] if preference else [],
                "function_reason": "Deterministic Memorist E2E analysis.",
                "notes": "local deterministic stub",
            }
        )
    dominant_overall = items[0]["dominant_function"] if items else "referential"
    return {
        "schema_version": "1.0",
        "prompt_id": "memorist.jakobson_sentence_analysis",
        "prompt_version": "3.0",
        "status": "ok",
        "warnings": [],
        "items": items,
        "analysis_level": "sentence",
        "model": "jakobson_six_factor",
        "input_language": "English",
        "sentence_count": len(items),
        "overall_summary": {
            "dominant_overall_function": dominant_overall,
            "secondary_overall_functions": [],
            "main_sender": "user",
            "main_receiver": "assistant",
            "main_context": (
                items[0]["six_factors"]["context_referent"]["value"]
                if items
                else "conversation"
            ),
            "main_code": "English",
            "main_contact_channel": "chat",
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # noqa: N802 - silence default logging
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._json(
                {
                    "object": "list",
                    "data": [{"id": MODEL_ID, "object": "model", "owned_by": "memorist-e2e"}],
                }
            )
            return
        if self.path.startswith("/__requests"):
            try:
                with open(LOG_PATH, encoding="utf-8") as log:
                    lines = [json.loads(line) for line in log if line.strip()]
            except FileNotFoundError:
                lines = []
            self._json({"requests": lines})
            return
        if self.path == "/health":
            self._json({"status": "ok"})
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("chat/completions"):
            self._json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        messages = payload.get("messages") or []
        safe_messages = [
            {
                "role": message.get("role"),
                "name": message.get("name"),
                "content_excerpt": _redact(str(message.get("content") or ""))[:2000],
                "has_memory_context_attachment": "<memory_context_attachment>"
                in str(message.get("content") or ""),
            }
            for message in messages
            if isinstance(message, dict)
        ]
        _record(
            {
                "path": self.path,
                "model": payload.get("model"),
                "stream": bool(payload.get("stream")),
                "authorization_header_present": bool(self.headers.get("Authorization")),
                "message_count": len(safe_messages),
                "messages": safe_messages,
            }
        )
        answer = _deterministic_answer(messages)
        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            for chunk in chunks:
                frame = {"id": "stub-1", "object": "chat.completion.chunk", "model": MODEL_ID, **chunk}
                self.wfile.write(f"data: {json.dumps(frame)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json(
            {
                "id": "stub-1",
                "object": "chat.completion",
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


def main() -> None:
    port = int(os.environ.get("PORT", "9800"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"memorist e2e model stub listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
