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
