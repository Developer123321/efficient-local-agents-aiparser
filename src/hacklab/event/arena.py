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
import json
import os
import shutil
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from hacklab.config import read_secret

DEFAULT_URL = "https://arena.ai-mission.eu"
MAX_BLOB_BYTES = 64_000_000
MAX_WORKSPACE_BYTES = 256_000_000
# A JSON reply: a run with its tasks, a manifest, a finished run with its answers.
MAX_REPLY_BYTES = 32_000_000


class ArenaError(RuntimeError):
    """A fixed, body-free description of a failed platform request."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def api_url(base_url: str) -> str:
    """The API prefix for a host, whether or not it already ends in /api/v1."""
    base = base_url.strip().rstrip("/")
    return base if base.endswith("/api/v1") else base + "/api/v1"


@dataclass(frozen=True)
class _Reply:
    """The body of an answered request, read up to a limit."""

    content: bytes

    def json(self) -> Any:
        return json.loads(self.content)


class Arena:
    """The Task API. Retries what passes: a lost connection, a 429, a 5xx.

    The API has no request ids, so a reply that is lost says nothing about
    whether the platform acted. A call that only reads, and one the platform
    accepts once only (`submit`), is sent again. A call that changes
    something is sent again only when the platform says it did not act (429,
    5xx) or was never reached; otherwise it ends as `arena_outcome_unknown`.
    """

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

    def _send(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        limit: int = MAX_REPLY_BYTES,
        repeatable: bool = True,
    ) -> _Reply:
        failure = ArenaError("arena_request_failed")
        for attempt in range(self.retries + 1):
            pause = min(0.5 * 2**attempt, 8.0)
            try:
                options = {} if body is None else {"json": body}
                with self._client.stream(
                    method, self.url + path, headers=self._headers, **options
                ) as response:
                    status = response.status_code
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > limit:
                            raise ArenaError("arena_reply_too_large")
                    retry_after = response.headers.get("Retry-After", "1")
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                failure = ArenaError("arena_unreachable")  # Nothing was sent.
            except httpx.HTTPError:
                if not repeatable:
                    # Sent, perhaps acted on, and the reply is lost.
                    raise ArenaError("arena_outcome_unknown") from None
                failure = ArenaError("arena_unreachable")
            else:
                if status < 400:
                    return _Reply(bytes(content))
                failure = ArenaError(f"arena_http_{status}", status)
                if status == 429:
                    try:
                        pause = max(pause, min(float(retry_after), 60))
                    except ValueError:
                        pass
                elif status < 500:
                    raise failure  # Our request is wrong, or the run is over: no retry helps.
            if attempt < self.retries:
                self.sleep(pause)
        raise failure

    def _json(self, method: str, path: str, body: Any = None, *, repeatable: bool = True) -> Any:
        try:
            return self._send(method, path, body, repeatable=repeatable).json()
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

    def call(
        self,
        run_id: str,
        task_id: str,
        tool: str,
        arguments: dict | None = None,
        *,
        repeatable: bool = True,
    ) -> dict:
        """`{ok: true, result}` or `{ok: false, error}`: a tool-level problem is no exception.

        `repeatable=False` is for a tool that changes something: a lost reply
        is then `arena_outcome_unknown`, never a second call.
        """
        path = f"/runs/{run_id}/tasks/{task_id}/tools/{tool}"
        return self._json("POST", path, arguments or {}, repeatable=repeatable)

    def submit(self, run_id: str, task_id: str, body: dict) -> dict:
        return self._json("POST", f"/runs/{run_id}/tasks/{task_id}/submit", body)

    def finish(self, run_id: str) -> dict:
        return self._json("POST", f"/runs/{run_id}/finish")

    # -- files -----------------------------------------------------------------

    def manifest(self, run_id: str) -> dict:
        """The run's distinct blobs and, per task, `path -> sha256`."""
        return self._json("GET", f"/runs/{run_id}/manifest")

    def task_manifest(self, run_id: str, task_id: str) -> dict[str, str]:
        """`path -> sha256` of one task's files as they are now, after its own changes."""
        found = self._json("GET", f"/runs/{run_id}/tasks/{task_id}/manifest")
        files = found.get("files") if isinstance(found, dict) else None
        if isinstance(files, dict):
            files = [{"path": path, "sha256": sha256} for path, sha256 in files.items()]
        if not isinstance(files, list):
            raise ArenaError("arena_manifest_invalid")
        listed: dict[str, str] = {}
        for entry in files:
            path = entry.get("path") if isinstance(entry, dict) else None
            sha256 = entry.get("sha256") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not isinstance(sha256, str):
                raise ArenaError("arena_manifest_invalid")
            listed[path] = sha256
        return listed

    def blob(self, run_id: str, sha256: str, task_id: str | None = None) -> bytes:
        hint = f"?task={task_id}" if task_id else ""
        path = f"/runs/{run_id}/blobs/{sha256}{hint}"
        return self._send("GET", path, limit=MAX_BLOB_BYTES).content

    def workspace_zip(self, run_id: str, task_id: str) -> bytes:
        path = f"/runs/{run_id}/tasks/{task_id}/workspace.zip"
        return self._send("GET", path, limit=MAX_WORKSPACE_BYTES).content

    # -- attacks and uploads ---------------------------------------------------

    def payloads(self) -> dict:
        return self._json("GET", "/payloads")

    def add_payload(self, carrier: str, goal: str, text: str, stage: str = "dev") -> dict:
        body = {"carrier": carrier, "goal": goal, "text": text, "stage": stage}
        # Never sent twice on a lost reply: a second copy would take a second slot.
        return self._json("POST", "/payloads", body, repeatable=False)

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


@dataclass(frozen=True)
class Changes:
    """What a refresh did to a task's files, as paths below its workspace."""

    written: tuple[str, ...] = ()  # New, or with new content.
    removed: tuple[str, ...] = ()


def _write(target: Path, data: bytes) -> None:
    """A unique temporary file, then a rename: a reader sees the old file or the new one."""
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".part-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class Mirror:
    """Task workspaces on disk: every distinct file fetched once, by its hash.

    A workspace is built from the task's current manifest and brought up to
    date by `refresh` after a tool changed something. Blobs are shared by all
    tasks and never change; each task has its own copies.
    """

    def __init__(self, arena: Arena, directory: Path):
        self.arena = arena
        self.directory = directory
        self.blobs = directory / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._manifests: dict[str, dict[str, dict[str, str]]] = {}
        # What each workspace built by this process holds: relative path -> sha256.
        self._held: dict[tuple[str, str], dict[str, str]] = {}
        self.downloaded = 0

    def _blob(self, run_id: str, sha256: str, task_id: str) -> bytes:
        """The bytes behind a content hash, checked: from the store, else fetched once."""
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ArenaError("arena_blob_id_invalid")
        target = self.blobs / sha256
        try:
            stored = target.read_bytes() if target.stat().st_size <= MAX_BLOB_BYTES else b""
        except OSError:
            stored = None
        if stored is not None:
            if hashlib.sha256(stored).hexdigest() == sha256:
                return stored
            target.unlink(missing_ok=True)  # Damaged on disk: fetched again.
        data = self.arena.blob(run_id, sha256, task_id)
        if len(data) > MAX_BLOB_BYTES or hashlib.sha256(data).hexdigest() != sha256:
            raise ArenaError("arena_blob_corrupt")
        _write(target, data)  # Parallel tasks share the store.
        with self._lock:
            self.downloaded += len(data)
        return data

    def _initial(self, run_id: str, task_id: str) -> dict[str, str]:
        """The task's files as the run began: one manifest for all tasks of the run."""
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

    def _root(self, run_id: str, task_id: str) -> Path:
        return self.directory / "runs" / _relative(run_id).name / _relative(task_id).name

    def _sync(
        self,
        root: Path,
        key: tuple[str, str],
        listed: dict[str, str],
        fetch: Callable[[str], bytes],
    ) -> Changes:
        """Make the directory hold exactly the listed files; what it already holds stays."""
        wanted = {_relative(path).as_posix(): str(sha256) for path, sha256 in listed.items()}
        with self._lock:
            held = self._held.setdefault(key, {})
        removed = sorted(name for name in held if name not in wanted)
        for name in removed:
            (root / name).unlink(missing_ok=True)
            held.pop(name, None)
            for parent in (root / name).parents:
                try:
                    if parent == root or any(parent.iterdir()):
                        break
                    parent.rmdir()
                except OSError:
                    break
        written = []
        size = 0
        for name, sha256 in sorted(wanted.items()):
            target = root / name
            if held.get(name) != sha256 or not target.is_file():
                data = fetch(sha256)
                target.parent.mkdir(parents=True, exist_ok=True)
                # A copy, not a link: a task must not be able to change the store.
                _write(target, data)
                held[name] = sha256
                written.append(name)
            size += target.stat().st_size
            if size > MAX_WORKSPACE_BYTES:
                raise ArenaError("arena_workspace_too_large")
        return Changes(tuple(written), tuple(removed))

    def _archive(self, root: Path, key: tuple[str, str]) -> Changes:
        """A platform without manifests: the whole workspace as one archive."""
        data = self.arena.workspace_zip(*key)
        contents: dict[str, bytes] = {}
        listed: dict[str, str] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = [info for info in archive.infolist() if not info.is_dir()]
                if sum(info.file_size for info in entries) > MAX_WORKSPACE_BYTES:
                    raise ArenaError("arena_workspace_too_large")
                for info in entries:
                    content = archive.read(info)
                    sha256 = hashlib.sha256(content).hexdigest()
                    listed[info.filename], contents[sha256] = sha256, content
        except zipfile.BadZipFile:
            raise ArenaError("arena_workspace_corrupt") from None
        return self._sync(root, key, listed, contents.__getitem__)

    def workspace(self, run_id: str, task_id: str) -> Path:
        """The task's files below one directory, as the platform holds them now.

        A workspace left by an earlier process is built again: after a
        restart the task may have changed its files on the platform.
        """
        root, key = self._root(run_id, task_id), (run_id, task_id)
        # Beside the workspace, not inside: the marker is no task document.
        complete = root.with_name(root.name + ".complete")
        with self._lock:
            built = key in self._held
        if built and complete.is_file():
            return root
        complete.unlink(missing_ok=True)
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        with self._lock:
            self._held[key] = {}
        try:
            try:
                listed = self.arena.task_manifest(run_id, task_id)
            except ArenaError:
                # No per-task manifest: a task that has not acted yet is as it began.
                listed = self._initial(run_id, task_id)
            self._sync(root, key, listed, lambda sha256: self._blob(run_id, sha256, task_id))
        except ArenaError as exc:
            if exc.status != 404:
                raise
            self._archive(root, key)
        complete.write_text("")
        return root

    def refresh(self, run_id: str, task_id: str) -> Changes:
        """Bring a built workspace up to the platform's current state.

        Called after a tool that may have changed the task's files. Only the
        current manifest counts here: the run's manifest describes the start.
        Raises when the state cannot be read or a file cannot be fetched; the
        files then are a mixture and must not be read as current.
        """
        root, key = self._root(run_id, task_id), (run_id, task_id)
        with self._lock:
            if key not in self._held:
                raise ArenaError("arena_workspace_not_built")
        try:
            listed = self.arena.task_manifest(run_id, task_id)
        except ArenaError as exc:
            if exc.status != 404:
                raise
            return self._archive(root, key)
        return self._sync(root, key, listed, lambda sha256: self._blob(run_id, sha256, task_id))
