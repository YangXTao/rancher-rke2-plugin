from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from .secrets import ensure_reference_only_config


class SQLiteStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        if not self._initialized:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS validated_configs (
                    config_digest TEXT PRIMARY KEY,
                    normalized_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    config_digest TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(config_digest)
                        REFERENCES validated_configs(config_digest)
                );
                CREATE TABLE IF NOT EXISTS preflights (
                    preflight_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    preflight_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    config_digest TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
                );
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    idempotency_key TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS run_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                """
            )
            connection.commit()
            self._initialized = True
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        return connection

    def save_config(
        self,
        config_digest: str,
        normalized: dict[str, Any],
        created_at: str,
    ) -> None:
        ensure_reference_only_config(normalized)
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO validated_configs(config_digest, normalized_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(config_digest) DO NOTHING
                """,
                (config_digest, payload, created_at),
            )

    def get_config(self, config_digest: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT normalized_json FROM validated_configs WHERE config_digest = ?",
                (config_digest,),
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["normalized_json"])
        try:
            ensure_reference_only_config(payload)
        except ValueError:
            return None
        return payload

    def save_plan(self, plan: dict[str, Any]) -> None:
        payload = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO plans(
                    plan_id, config_digest, plan_json, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan["plan_id"],
                    plan["config_digest"],
                    payload,
                    plan["created_at"],
                    plan["expires_at"],
                ),
            )

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json FROM plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return json.loads(row["plan_json"]) if row else None

    def save_preflight(self, preflight: dict[str, Any]) -> None:
        payload = json.dumps(preflight, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO preflights(
                    preflight_id, plan_id, config_digest, preflight_json,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    preflight["preflight_id"],
                    preflight["plan_id"],
                    preflight["config_digest"],
                    payload,
                    preflight["created_at"],
                    preflight["expires_at"],
                ),
            )

    def get_preflight(self, preflight_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT preflight_json FROM preflights WHERE preflight_id = ?",
                (preflight_id,),
            ).fetchone()
        return json.loads(row["preflight_json"]) if row else None

    def save_run(self, run: dict[str, Any]) -> None:
        payload = json.dumps(run, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs(run_id, plan_id, config_digest, run_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["plan_id"],
                    run["config_digest"],
                    payload,
                    run["created_at"],
                    run["updated_at"],
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row["run_json"]) if row else None

    def latest_succeeded_component_run(
        self, config_digest: str, component: str
    ) -> dict[str, Any] | None:
        """Return the newest successful run containing the component.

        A component can succeed either as a standalone single-component run or
        as one stage of a succeeded workflow, so the lookup matches both the
        run state and the persisted component state.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_json FROM runs
                WHERE config_digest = ?
                ORDER BY created_at DESC
                """,
                (config_digest,),
            ).fetchall()
        for row in rows:
            run = json.loads(row["run_json"])
            if run.get("state") != "SUCCEEDED":
                continue
            if component not in run.get("target_components", []):
                continue
            states = {
                item.get("component"): item.get("state")
                for item in run.get("component_states", [])
            }
            if states.get(component) == "SUCCEEDED":
                return run
        return None

    def update_run(self, run: dict[str, Any]) -> None:
        payload = json.dumps(run, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET run_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (payload, run["updated_at"], run["run_id"]),
            )

    def save_idempotency_key(
        self, idempotency_key: str, request_fingerprint: str, run_id: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_keys(idempotency_key, request_fingerprint, run_id)
                VALUES (?, ?, ?)
                """,
                (idempotency_key, request_fingerprint, run_id),
            )

    def get_idempotency_key(self, idempotency_key: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_fingerprint, run_id
                FROM idempotency_keys
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
        return dict(row) if row else None

    def append_run_event(
        self, run_id: str, created_at: str, event: dict[str, Any]
    ) -> str:
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO run_events(run_id, created_at, event_json)
                VALUES (?, ?, ?)
                """,
                (run_id, created_at, payload),
            )
        return str(cursor.lastrowid)

    def get_run_events(
        self, run_id: str, after_cursor: str | None, limit: int
    ) -> list[dict[str, Any]]:
        after = int(after_cursor or "0")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, created_at, event_json
                FROM run_events
                WHERE run_id = ? AND event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (run_id, after, limit),
            ).fetchall()
        return [
            {
                "cursor": str(row["event_id"]),
                "created_at": row["created_at"],
                **json.loads(row["event_json"]),
            }
            for row in rows
        ]
