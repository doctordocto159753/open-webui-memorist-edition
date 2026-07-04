from __future__ import annotations

# ruff: noqa: E501
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID = "memorist.jakobson_sentence_analysis"
PROMPT_PACK_VERSION = "2.0"


def structured_jakobson_response(
    text: str = "Remember to use Jira for workflow tracking.",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "prompt_id": JAKOBSON_SENTENCE_ANALYSIS_PROMPT_ID,
        "prompt_version": PROMPT_PACK_VERSION,
        "status": "ok",
        "warnings": [],
        "items": [],
        "analysis_level": "sentence",
        "model": "jakobson_six_factor",
        "input_language": "mixed",
        "sentence_count": 1,
        "sentences": [
            {
                "id": 1,
                "text": text,
                "six_factors": {
                    "sender_addresser": {"value": "user", "evidence": text, "confidence": "high"},
                    "receiver_addressee": {
                        "value": "assistant",
                        "evidence": text,
                        "confidence": "medium",
                    },
                    "message": {"value": text, "evidence": text, "confidence": "high"},
                    "context_referent": {
                        "value": "workflow tracking",
                        "evidence": text,
                        "confidence": "high",
                    },
                    "code": {
                        "value": "English workflow terminology",
                        "evidence": "Jira",
                        "confidence": "medium",
                    },
                    "contact_channel": {"value": "chat", "evidence": text, "confidence": "medium"},
                },
                "dominant_function": "conative",
                "secondary_functions": ["referential"],
                "function_reason": "The sentence asks the assistant to remember a workflow preference.",
                "notes": "fake provider",
            }
        ],
        "overall_summary": {
            "dominant_overall_function": "conative",
            "secondary_overall_functions": ["referential"],
            "main_sender": "user",
            "main_receiver": "assistant",
            "main_context": "workflow tracking",
            "main_code": "English workflow terminology",
            "main_contact_channel": "chat",
        },
    }


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        text = "Remember to use Jira for workflow tracking."
        try:
            content = payload["messages"][-1]["content"]
            user_payload = json.loads(content)
            text = user_payload["sentences"][0]["text"]
        except Exception:
            pass
        response = {
            "id": "fake-memory-extraction-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(structured_jakobson_response(text)),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 42, "completion_tokens": 84, "total_tokens": 126},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "0.0.0.0", port: int = 31415) -> None:
    ThreadingHTTPServer((host, port), FakeOpenAIHandler).serve_forever()


if __name__ == "__main__":
    run()
