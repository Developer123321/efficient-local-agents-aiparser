"""A stand-in model endpoint for plumbing tests, above all inside the offline container.

It answers every chat request with a fixed abstention, every Jev question
with certainty and every embedding request with the same vector. That proves
the request path, credentials handling and the output contract without a
network or a key. It says nothing about a model.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

REPLY = {
    "action": "final",
    "result": {
        "status": "abstain",
        "answer": "",
        "evidence": [],
        "actions": [],
        "confidence": 0,
        "reason": "Stand-in endpoint: no model was consulted.",
    },
}
MAX_BODY = 8_000_000


class Handler(BaseHTTPRequestHandler):
    server_version = "hacklab-mock"

    def log_message(self, *args: Any) -> None:  # Request bodies never reach a log.
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError
            request = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"error": "invalid_request"})
            return
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._send(
                200,
                {
                    "model": "mock-small",
                    "choices": [
                        {"message": {"content": json.dumps(REPLY)}, "finish_reason": "stop"}
                    ],
                    # Fixed synthetic numbers, not a token count of the request.
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        elif self.path.rstrip("/").endswith("/systemone"):
            answers = {name: {"type": "noul", "noul": 1.0} for name in request.get("questions", {})}
            self._send(
                200,
                {
                    "model": "mock-jev",
                    "answers": answers,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        elif self.path.rstrip("/").endswith("/embeddings"):
            texts = request.get("input", [])
            texts = texts if isinstance(texts, list) else [texts]
            self._send(
                200,
                {
                    "model": "mock-embedding",
                    "data": [
                        {"index": index, "embedding": [1.0, 0.0]} for index in range(len(texts))
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        else:
            self._send(404, {"error": "unknown_path"})


def serve(host: str, port: int) -> None:
    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()
