"""SQLite snapshot store. Raw evidence is append-only by run."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import normalize
from .models import Account, Facility, MatchResult, Proposal


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
CREATE TABLE IF NOT EXISTS match_results (
  match_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  facility_key TEXT,
  classification TEXT NOT NULL,
  confidence TEXT NOT NULL,
  selected_account_ids_json TEXT NOT NULL,
  candidate_evidence_json TEXT NOT NULL,
  explanation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
  proposal_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  fingerprint TEXT NOT NULL UNIQUE,
  facility_key TEXT NOT NULL,
  classification TEXT NOT NULL,
  confidence TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending',
  writable INTEGER NOT NULL,
  evidence_json TEXT NOT NULL,
  current_json TEXT NOT NULL,
  desired_json TEXT NOT NULL,
  reviewer_reason TEXT,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_steps (
  step_id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(proposal_id),
  sequence INTEGER NOT NULL,
  operation TEXT NOT NULL,
  target_id TEXT NOT NULL,
  request_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending',
  UNIQUE(proposal_id, sequence)
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

    def save_matches(self, run_id: int, results: list[MatchResult]) -> None:
        for result in results:
            facility = result.facility
            key = (
                normalize.facility_key(facility.street, facility.city, facility.state, facility.zip_code)
                if facility else None
            )
            self.connection.execute(
                """INSERT INTO match_results(
                     run_id, facility_key, classification, confidence,
                     selected_account_ids_json, candidate_evidence_json, explanation
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, key, result.classification, result.confidence,
                    _canonical([item.account_id for item in result.selected_accounts]),
                    _canonical([item.to_dict() for item in result.candidates]),
                    result.explanation,
                ),
            )
        self.connection.commit()

    def save_proposals(self, run_id: int, proposals: list[Proposal]) -> int:
        inserted = 0
        for proposal in proposals:
            cursor = self.connection.execute(
                """INSERT OR IGNORE INTO proposals(
                     run_id, fingerprint, facility_key, classification, confidence,
                     writable, evidence_json, current_json, desired_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id, proposal.fingerprint, proposal.facility_key,
                    proposal.classification, proposal.confidence, int(proposal.writable),
                    _canonical(proposal.evidence), _canonical(proposal.current),
                    _canonical(proposal.desired),
                ),
            )
            if not cursor.rowcount:
                continue
            inserted += 1
            proposal_id = int(cursor.lastrowid)
            for step in proposal.steps:
                self.connection.execute(
                    """INSERT INTO proposal_steps(
                         proposal_id, sequence, operation, target_id, request_json
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (proposal_id, step.sequence, step.operation, step.target_id, _canonical(step.request)),
                )
        self.connection.commit()
        return inserted

    def close(self) -> None:
        self.connection.close()
