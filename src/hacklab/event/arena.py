"""The Harness Arena Task API: runs, task workspaces, tools, submissions, attacks.

One small HTTP client and a local mirror of a task's files. The mirror exists
because the platform's `read` and `search` serve plain text only, cost a
request each and are rate limited; local files are parsed by `hacklab.tools`
in every format and cost nothing. Each file is fetched once by its content
hash and shared by all tasks of a run.

Address and key come from the environment: ARENA_BASE_URL (the host; /api/v1
is appended) and ARENA_API_KEY, the team's platform key.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from hacklab.config import read_secret

DEFAULT_URL = "https://arena.ai-mission.eu"
MAX_BLOB_BYTES = 64_000_000
MAX_WORKSPACE_BYTES = 256_000_000


class ArenaError(RuntimeError):
    """A fixed, body-free description of a failed platform request."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_url(base_url: str) -> str:
    """The API prefix for a host, whether or not it already ends in /api/v1."""
    base = base_url.strip().rstrip("/")
    return base if base.endswith("/api/v1") else base + "/api/v1"


class Arena:
    """The Task API. Retries what passes: a lost connection, a 429, a 5xx."""

    def __init__(
        self,
        base_url: str,
        key: str,
        *,
        client: httpx.Client | None = None,
        retries: int = 4,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not key:
            raise ArenaError("arena_key_missing")
        self.url = api_url(base_url)
        self.retries = retries
        self.sleep = sleep
        self._headers = {"Authorization": f"Bearer {key}"}
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None

    @classmethod
    def from_env(cls, **options: Any) -> Arena:
        return cls(
            os.environ.get("ARENA_BASE_URL") or DEFAULT_URL, read_secret("ARENA_API_KEY"), **options
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _send(self, method: str, path: str, body: Any = None) -> httpx.Response:
        failure = ArenaError("arena_request_failed")
        for attempt in range(self.retries + 1):
            pause = min(0.5 * 2**attempt, 8.0)
            try:
                options = {} if body is None else {"json": body}
                response = self._client.request(
                    method, self.url + path, headers=self._headers, **options
                )
            except httpx.HTTPError:
                failure = ArenaError("arena_unreachable")
            else:
                status = response.status_code
                if status < 400:
                    return response
                failure = ArenaError(f"arena_http_{status}", status)
                if status == 429:
                    try:
                        pause = max(pause, min(float(response.headers.get("Retry-After", 1)), 60))
                    except ValueError:
                        pass
                elif status < 500:
                    raise failure  # Our request is wrong, or the run is over: no retry helps.
            if attempt < self.retries:
                self.sleep(pause)
        raise failure

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        try:
            return self._send(method, path, body).json()
        except ValueError as exc:
            raise ArenaError("arena_reply_not_json") from exc

    # -- runs and tasks --------------------------------------------------------

    def me(self) -> dict:
        return self._json("GET", "/me")

    def tools(self) -> list[dict]:
        """`[{name, description, args}]`, the platform's authoritative tool list."""
        found = self._json("GET", "/tools")
        items = found.get("tools", []) if isinstance(found, dict) else found
        return [item for item in items if isinstance(item, dict) and item.get("name")]

    def start_run(
        self, kind: str = "dev", seed: int | None = None, meta: dict | None = None
    ) -> dict:
        body: dict[str, Any] = {"kind": kind}
        if seed is not None:
            body["seed"] = seed
        if meta:
            body["meta"] = meta
        return self._json("POST", "/runs", body)

    def run(self, run_id: str) -> dict:
        return self._json("GET", f"/runs/{run_id}")

    def runs(self) -> dict:
        return self._json("GET", "/runs")

    def call(self, run_id: str, task_id: str, tool: str, arguments: dict | None = None) -> dict:
        """`{ok: true, result}` or `{ok: false, error}`: a tool-level problem is no exception."""
        return self._json("POST", f"/runs/{run_id}/tasks/{task_id}/tools/{tool}", arguments or {})

    def submit(self, run_id: str, task_id: str, body: dict) -> dict:
        return self._json("POST", f"/runs/{run_id}/tasks/{task_id}/submit", body)

    def finish(self, run_id: str) -> dict:
        return self._json("POST", f"/runs/{run_id}/finish")

    # -- files -----------------------------------------------------------------

    def manifest(self, run_id: str) -> dict:
        """The run's distinct blobs and, per task, `path -> sha256`."""
        return self._json("GET", f"/runs/{run_id}/manifest")

    def blob(self, run_id: str, sha256: str, task_id: str | None = None) -> bytes:
        hint = f"?task={task_id}" if task_id else ""
        return self._send("GET", f"/runs/{run_id}/blobs/{sha256}{hint}").content

    def workspace_zip(self, run_id: str, task_id: str) -> bytes:
        return self._send("GET", f"/runs/{run_id}/tasks/{task_id}/workspace.zip").content

    # -- attacks and uploads ---------------------------------------------------

    def payloads(self) -> dict:
        return self._json("GET", "/payloads")

    def add_payload(self, carrier: str, goal: str, text: str, stage: str = "dev") -> dict:
        body = {"carrier": carrier, "goal": goal, "text": text, "stage": stage}
        return self._json("POST", "/payloads", body)

    def change_payload(self, payload_id: str, change: dict) -> dict:
        return self._json("PATCH", f"/payloads/{payload_id}", change)

    def submissions(self) -> dict:
        return self._json("GET", "/submissions")

    def prepare_submission(self) -> dict:
        return self._json("POST", "/submissions")

    def submission_ready(self, submission_id: str) -> dict:
        return self._json("POST", f"/submissions/{submission_id}/ready")


def _relative(path: str) -> PurePosixPath:
    """A workspace path as a path below the task directory, or an ArenaError."""
    parts = PurePosixPath(path.replace("\\", "/")).parts
    parts = tuple(part for part in parts if part not in ("/", "."))
    if not parts or ".." in parts or any("\x00" in part for part in parts):
        raise ArenaError("arena_path_invalid")
    return PurePosixPath(*parts)


class Mirror:
    """Task workspaces on disk: every distinct file fetched once, by its hash."""

    def __init__(self, arena: Arena, directory: Path):
        self.arena = arena
        self.directory = directory
        self.blobs = directory / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._manifests: dict[str, dict[str, dict[str, str]]] = {}
        self.downloaded = 0

    def _blob(self, run_id: str, sha256: str, task_id: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ArenaError("arena_blob_id_invalid")
        target = self.blobs / sha256
        if target.is_file():
            return target
        data = self.arena.blob(run_id, sha256, task_id)
        if len(data) > MAX_BLOB_BYTES or hashlib.sha256(data).hexdigest() != sha256:
            raise ArenaError("arena_blob_corrupt")
        # A unique temporary file, then a rename: parallel tasks share the store.
        handle, temporary = tempfile.mkstemp(dir=self.blobs, prefix=sha256[:16], suffix=".part")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        with self._lock:
            self.downloaded += len(data)
        return target

    def _files(self, run_id: str, task_id: str) -> dict[str, str]:
        with self._lock:
            known = self._manifests.get(run_id)
        if known is None:
            listed = self.arena.manifest(run_id)
            known = {
                str(task.get("task_id")): dict(task.get("files") or {})
                for task in listed.get("tasks", [])
                if isinstance(task, dict)
            }
            with self._lock:
                self._manifests[run_id] = known
        if task_id not in known:
            raise ArenaError("arena_task_not_in_manifest")
        return known[task_id]

    def workspace(self, run_id: str, task_id: str) -> Path:
        """The task's files below one directory, as they were when the task began."""
        root = self.directory / "runs" / _relative(run_id).name / _relative(task_id).name
        # Beside the workspace, not inside: the marker is no task document.
        complete = root.with_name(root.name + ".complete")
        if complete.is_file():
            return root
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        try:
            for path, sha256 in sorted(self._files(run_id, task_id).items()):
                target = root / _relative(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                # A copy, not a link: a task must not be able to change the store.
                shutil.copyfile(self._blob(run_id, str(sha256), task_id), target)
        except ArenaError as exc:
            if exc.status != 404:
                raise
            self._unzip(self.arena.workspace_zip(run_id, task_id), root)
        complete.write_text("")
        return root

    @staticmethod
    def _unzip(data: bytes, root: Path) -> None:
        """A platform without manifests: the whole workspace as one archive."""
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [info for info in archive.infolist() if not info.is_dir()]
            if sum(info.file_size for info in entries) > MAX_WORKSPACE_BYTES:
                raise ArenaError("arena_workspace_too_large")
            for info in entries:
                target = root / _relative(info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
