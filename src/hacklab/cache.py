"""Development-only replay of identical model requests.

The team's request budget is limited. With temperature 0 an unchanged prompt
gets the same reply, so re-running a benchmark after changing one tool pays
only for the requests that actually changed. The cache holds model replies:
keep it in the private state directory and switch it off for the final run,
where every request must be live.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hacklab.tracking import _private_file


class ResponseCache:
    def __init__(self, path: Path, mode: str):
        if mode not in {"readwrite", "replay"}:
            raise ValueError("Cache mode must be readwrite or replay")
        self.mode = mode
        self.path = Path(path).expanduser().absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private_file(self.path)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, "
                "created_at TEXT NOT NULL, provider TEXT NOT NULL, payload_json TEXT NOT NULL)"
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def key(provider: str, endpoint: str, body: dict[str, Any], salt: str = "") -> str:
        """Identity of a request: where it goes and its exact body. No credentials."""
        canonical = json.dumps([provider, endpoint, body, salt], sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT payload_json FROM responses WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, provider: str, payload: dict[str, Any]) -> None:
        if self.mode != "readwrite":
            return
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO responses VALUES (?,?,?,?)",
                (key, datetime.now(UTC).isoformat(), provider, json.dumps(payload)),
            )

    def stats(self) -> dict[str, int]:
        with self._db() as db:
            entries, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(payload_json)),0) FROM responses"
            ).fetchone()
        return {"entries": entries, "payload_bytes": size}

    def clear(self) -> int:
        with self._db() as db:
            return db.execute("DELETE FROM responses").rowcount
