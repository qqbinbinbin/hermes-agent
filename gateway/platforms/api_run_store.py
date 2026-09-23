"""Persistent idempotency for native API runs; never replay uncertain work."""

import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


class RunIdentityConflict(ValueError):
    pass


class APIRunStore:
    def __init__(self, home):
        path = Path(home) / "api_runs.db"
        # Persistence is mandatory for keyed requests, unlike optional history.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS api_runs (
            request_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            run_id TEXT NOT NULL UNIQUE, status_json TEXT NOT NULL
        )""")
        self._conn.commit()

    def claim(self, key, payload):
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        payload_hash = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()).hexdigest()
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT payload_hash, status_json FROM api_runs WHERE request_key=?",
                (key_hash,),
            ).fetchone()
            if row:
                if row[0] != payload_hash:
                    raise RunIdentityConflict("run_idempotency_conflict")
                return False, json.loads(row[1])
            status = {"object": "hermes.run", "run_id": f"run_{uuid.uuid4().hex}",
                      "status": "accepted", "created_at": time.time()}
            self._conn.execute("INSERT INTO api_runs VALUES (?, ?, ?, ?)",
                               (key_hash, payload_hash, status["run_id"], json.dumps(status)))
            return True, status

    def update(self, status):
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE api_runs SET status_json=? WHERE run_id=?",
                (json.dumps(status), status["run_id"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("run_identity_missing")

    def get(self, run_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT status_json FROM api_runs WHERE run_id=?", (run_id,),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def close(self):
        with self._lock:
            self._conn.close()


def detached_run_status(status):
    """A surviving record is not proof an interrupted executor is still alive."""
    if status["status"] in {"completed", "failed", "cancelled"}:
        return status
    return {**status, "status": "unknown", "persisted_status": status["status"],
            "reconciliation_required": True}
