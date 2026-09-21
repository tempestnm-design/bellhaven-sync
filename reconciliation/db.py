"""SQLite snapshot store. Raw evidence is append-only by run."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import Account, Facility


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY,
  operator_key TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
  error TEXT
);
CREATE TABLE IF NOT EXISTS website_snapshots (
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  source_url TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id, source_url)
);
CREATE TABLE IF NOT EXISTS account_snapshots (
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  account_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id, account_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SnapshotStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.executescript(SCHEMA)

    @contextmanager
    def run(self, operator_key: str) -> Iterator[int]:
        cursor = self.connection.execute(
            "INSERT INTO runs(operator_key, started_at, status) VALUES (?, ?, 'running')",
            (operator_key, _now()),
        )
        run_id = int(cursor.lastrowid)
        self.connection.commit()
        try:
            yield run_id
        except Exception as exc:
            self.connection.execute(
                "UPDATE runs SET finished_at=?, status='failed', error=? WHERE run_id=?",
                (_now(), str(exc)[:2000], run_id),
            )
            self.connection.commit()
            raise
        else:
            self.connection.execute(
                "UPDATE runs SET finished_at=?, status='succeeded' WHERE run_id=?",
                (_now(), run_id),
            )
            self.connection.commit()

    def save_facilities(self, run_id: int, facilities: list[Facility]) -> None:
        for facility in facilities:
            payload = _canonical(facility.to_dict())
            self.connection.execute(
                "INSERT INTO website_snapshots VALUES (?, ?, ?, ?)",
                (run_id, facility.source_url, _hash(payload), payload),
            )
        self.connection.commit()

    def save_accounts(self, run_id: int, accounts: list[Account]) -> None:
        for account in accounts:
            payload = _canonical(account.raw)
            self.connection.execute(
                "INSERT INTO account_snapshots VALUES (?, ?, ?, ?)",
                (run_id, account.account_id, _hash(payload), payload),
            )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
