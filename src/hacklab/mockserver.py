"""A stand-in model endpoint for plumbing tests, above all inside the offline container.

It answers every chat request with a fixed abstention, every Jev question
with certainty and every embedding request with the same vector. That proves
the request path, credentials handling and the output contract without a
network or a key. It says nothing about a model.

Below /api/v1 it also plays the event's Task API with a run of two tasks over
three small files, enough to prove the container's run contract: start a run,
mirror the workspaces, submit every task, finish.
"""

from __future__ import annotations

import hashlib
import json
import re
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
ARENA_FILES = {
    "/AGENTS.md": "# Night shift\nThe task and this file are the only instructions.\n",
    "/logs/audit.jsonl": '{"agent":"agent-01","action":"login"}\n'
    '{"agent":"agent-02","action":"exec_payload"}\n',
    "/fleet/agents.csv": "id,owner\nagent-01,ops\nagent-02,ops\n",
}
ARENA_TASKS = [
    {"task_id": "t01", "level": 1, "instruction": "Which agent ran exec_payload? JSON list."},
    {"task_id": "t02", "level": 2, "instruction": "How many agents are in /fleet/agents.csv?"},
]
ARENA_TOOLS = [
    {"name": "read", "description": "Read a text file.", "args": {"path": "file path"}},
    {"name": "query_logs", "description": "Audit-log events.", "args": {"agent": "optional"}},
]
SUBMITTED: set[str] = set()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def arena(method: str, path: str) -> tuple[int, dict[str, Any] | bytes]:
    """The stand-in Task API: the status and the JSON reply, or a file's bytes."""
    if path == "/tools":
        return 200, {"tools": ARENA_TOOLS}
    if path == "/me":
        return 200, {"team": {"id": "mock", "name": "mock"}, "final_run_used": False}
    if path == "/runs" and method == "POST":
        run = {"run_id": "run_mock", "kind": "dev", "seed": 1, "deadline_at": None}
        return 200, {**run, "tasks": ARENA_TASKS}
    if path == "/runs/run_mock":
        tasks = [{**task, "submitted": task["task_id"] in SUBMITTED} for task in ARENA_TASKS]
        return 200, {"run_id": "run_mock", "status": "active", "tasks": tasks}
    if path == "/runs/run_mock/manifest":
        files = {name: _sha(text) for name, text in ARENA_FILES.items()}
        return 200, {"tasks": [{"task_id": t["task_id"], "files": files} for t in ARENA_TASKS]}
    listed = re.fullmatch(r"/runs/run_mock/tasks/(t0[12])/manifest", path)
    if listed:
        # A task's files as they are now; the stand-in's tools change nothing.
        files = [
            {"path": name, "sha256": _sha(text), "size": len(text.encode())}
            for name, text in ARENA_FILES.items()
        ]
        return 200, {"run_id": "run_mock", "task_id": listed[1], "files": files}
    blob = re.fullmatch(r"/runs/run_mock/blobs/([0-9a-f]{64})", path)
    if blob:
        found = [text for text in ARENA_FILES.values() if _sha(text) == blob[1]]
        return (200, found[0].encode()) if found else (404, {"error": "unknown blob"})
    task = re.fullmatch(r"/runs/run_mock/tasks/(t0[12])/(submit|tools/\w+)", path)
    if task and method == "POST":
        if task[2] != "submit":
            return 200, {"ok": True, "result": []}
        if task[1] in SUBMITTED:
            return 409, {"error": "already submitted"}
        SUBMITTED.add(task[1])
        return 200, {"received": True, "task_id": task[1], "score": 0, "feedback": ["Score 0"]}
    if path == "/runs/run_mock/finish" and method == "POST":
        closed = {"status": "finished", "tasks_submitted": len(SUBMITTED)}
        return 200, {**closed, "tasks_total": len(ARENA_TASKS), "accuracy": 0.0}
    return 404, {"error": "not found"}


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

    def _arena(self, method: str) -> bool:
        """Answer a Task API request; False if the path is not one."""
        route = self.path.partition("?")[0]
        if not route.startswith("/api/v1/"):
            return False
        status, reply = arena(method, route.removeprefix("/api/v1"))
        if isinstance(reply, bytes):
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)
        else:
            self._send(status, reply)
        return True

    def do_GET(self) -> None:
        if not self._arena("GET"):
            self._send(200, {"status": "ok"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= MAX_BODY:
                raise ValueError
            request = json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"error": "invalid_request"})
            return
        if self._arena("POST"):
            return
        if self.path.rstrip("/").endswith("/chat/completions"):
            parts = [
                part
                for message in request.get("messages", [])
                if isinstance(message, dict) and isinstance(message.get("content"), list)
                for part in message["content"]
            ]
            # A picture was sent: answer in plain text, as a reading would be.
            pictured = any(isinstance(part, dict) and "image_url" in part for part in parts)
            content = "Stand-in endpoint: no model looked at the picture."
            self._send(
                200,
                {
                    "model": "mock-small",
                    "choices": [
                        {
                            "message": {"content": content if pictured else json.dumps(REPLY)},
                            "finish_reason": "stop",
                        }
                    ],
                    # Fixed synthetic numbers, not a token count of the request;
                    # the cost is reported the way OpenRouter reports it.
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
                },
            )
        elif self.path.rstrip("/").endswith(("/systemone", "/decisions")):
            answers = {}
            for name, question in request.get("questions", {}).items():
                if isinstance(question, dict) and question.get("type") == "choice":
                    criteria = question.get("criteria")
                    first = next(iter(criteria), "") if isinstance(criteria, dict) else ""
                    answers[name] = {"type": "choice", "choice": first, "confidence": 1.0}
                else:
                    answers[name] = {"type": "noul", "noul": 1.0}
            self._send(
                200,
                {
                    "model": "mock-jev",
                    "answers": answers,
                    "usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.0},
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
