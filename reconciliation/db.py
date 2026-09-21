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
  reviewer_name TEXT,
  decided_at TEXT,
  execution_started_at TEXT,
  execution_finished_at TEXT
);
CREATE TABLE IF NOT EXISTS proposal_steps (
  step_id INTEGER PRIMARY KEY,
  proposal_id INTEGER NOT NULL REFERENCES proposals(proposal_id),
  sequence INTEGER NOT NULL,
  operation TEXT NOT NULL,
  target_id TEXT NOT NULL,
  request_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending',
  response_status INTEGER,
  response_json TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  started_at TEXT,
  finished_at TEXT,
  UNIQUE(proposal_id, sequence)
);
CREATE TABLE IF NOT EXISTS audit_events (
  event_id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  proposal_id INTEGER REFERENCES proposals(proposal_id),
  event_type TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
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
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        additions = {
            "proposals": {
                "reviewer_name": "TEXT",
                "execution_started_at": "TEXT",
                "execution_finished_at": "TEXT",
            },
            "proposal_steps": {
                "response_status": "INTEGER",
                "response_json": "TEXT",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "started_at": "TEXT",
                "finished_at": "TEXT",
            },
        }
        for table, columns in additions.items():
            existing = {
                row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")
            }
            for column, definition in columns.items():
                if column not in existing:
                    self.connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    )
        self.connection.commit()

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
                # Evidence and reviewer-facing wording may improve without changing the
                # logical mutation fingerprint. Refresh only undecided queue items;
                # decided proposals retain the evidence shown at decision time.
                self.connection.execute(
                    """UPDATE proposals SET confidence = ?, evidence_json = ?
                       WHERE fingerprint = ? AND status = 'Pending'""",
                    (proposal.confidence, _canonical(proposal.evidence), proposal.fingerprint),
                )
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

    def list_proposals(self, filters: dict[str, str] | None = None) -> list[dict[str, object]]:
        filters = filters or {}
        clauses: list[str] = []
        values: list[object] = []
        allowed = {"status", "classification", "confidence", "run_id"}
        for key in allowed:
            value = filters.get(key, "").strip()
            if value:
                clauses.append(f"p.{key} = ?")
                values.append(int(value) if key == "run_id" else value)
        facility = filters.get("facility", "").strip()
        if facility:
            clauses.append(
                "(p.facility_key LIKE ? OR json_extract(p.evidence_json, '$.website.name') LIKE ?)"
            )
            values.extend([f"%{facility}%", f"%{facility}%"])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.connection.execute(
            f"""SELECT p.*, COUNT(s.step_id) AS step_count,
                       SUM(CASE WHEN s.status='Applied' THEN 1 ELSE 0 END) AS applied_steps
                FROM proposals p LEFT JOIN proposal_steps s ON s.proposal_id=p.proposal_id
                {where}
                GROUP BY p.proposal_id
                ORDER BY CASE p.status WHEN 'Pending' THEN 0 WHEN 'Approved' THEN 1 ELSE 2 END,
                         p.proposal_id""",
            values,
        ).fetchall()
        return [self._proposal_dict(row, include_steps=False) for row in rows]

    def get_proposal(self, proposal_id: int) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if not row:
            return None
        proposal = self._proposal_dict(row, include_steps=False)
        steps = self.connection.execute(
            "SELECT * FROM proposal_steps WHERE proposal_id=? ORDER BY sequence",
            (proposal_id,),
        ).fetchall()
        proposal["steps"] = [self._step_dict(step) for step in steps]
        proposal["audit_events"] = [
            {
                **dict(event),
                "detail": json.loads(event["detail_json"]),
            }
            for event in self.connection.execute(
                "SELECT * FROM audit_events WHERE proposal_id=? ORDER BY event_id",
                (proposal_id,),
            )
        ]
        return proposal

    def _proposal_dict(self, row: sqlite3.Row, *, include_steps: bool) -> dict[str, object]:
        result = dict(row)
        for column in ("evidence_json", "current_json", "desired_json"):
            result[column.removesuffix("_json")] = json.loads(result[column])
        result["writable"] = bool(result["writable"])
        result.pop("evidence_json", None)
        result.pop("current_json", None)
        result.pop("desired_json", None)
        return result

    @staticmethod
    def _step_dict(row: sqlite3.Row) -> dict[str, object]:
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        response = result.pop("response_json")
        result["response"] = json.loads(response) if response else None
        return result

    def decide_proposal(
        self, proposal_id: int, decision: str, reviewer_name: str, reason: str
    ) -> None:
        if decision not in {"Approved", "Rejected"}:
            raise ValueError("Decision must be Approved or Rejected")
        self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            "SELECT run_id, status, writable FROM proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if not row:
            self.connection.rollback()
            raise KeyError(proposal_id)
        if row["status"] != "Pending":
            self.connection.rollback()
            raise ValueError("Only Pending proposals can be decided")
        if decision == "Approved" and not row["writable"]:
            self.connection.rollback()
            raise ValueError("Non-writable findings cannot be approved for execution")
        self.connection.execute(
            """UPDATE proposals SET status=?, reviewer_name=?, reviewer_reason=?, decided_at=?
               WHERE proposal_id=?""",
            (decision, reviewer_name.strip() or "Local Reviewer", reason.strip(), _now(), proposal_id),
        )
        self._audit(row["run_id"], proposal_id, f"proposal_{decision.lower()}", {
            "reviewer_name": reviewer_name.strip() or "Local Reviewer",
            "reason": reason.strip(),
        })
        self.connection.commit()

    def _audit(
        self, run_id: int, proposal_id: int | None, event_type: str, detail: dict[str, object]
    ) -> None:
        self.connection.execute(
            """INSERT INTO audit_events(run_id, proposal_id, event_type, detail_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, proposal_id, event_type, _canonical(detail), _now()),
        )

    def set_proposal_status(
        self, proposal_id: int, status: str, event_type: str, detail: dict[str, object]
    ) -> None:
        row = self.connection.execute(
            "SELECT run_id FROM proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        if not row:
            raise KeyError(proposal_id)
        fields = ["status=?"]
        values: list[object] = [status]
        if status == "Applying":
            fields.append("execution_started_at=?")
            values.append(_now())
        if status in {"Applied", "Partially Applied", "Conflict", "Failed"}:
            fields.append("execution_finished_at=?")
            values.append(_now())
        values.append(proposal_id)
        self.connection.execute(
            f"UPDATE proposals SET {', '.join(fields)} WHERE proposal_id=?", values
        )
        self._audit(row["run_id"], proposal_id, event_type, detail)
        self.connection.commit()

    def start_step(self, step_id: int) -> None:
        self.connection.execute(
            """UPDATE proposal_steps SET status='Applying', attempts=attempts+1, started_at=?
               WHERE step_id=?""",
            (_now(), step_id),
        )
        self.connection.commit()

    def finish_step(
        self, step_id: int, status: str, response_status: int | None,
        response: object, *, target_id: str | None = None,
    ) -> None:
        assignments = ["status=?", "response_status=?", "response_json=?", "finished_at=?"]
        values: list[object] = [status, response_status, _canonical(response), _now()]
        if target_id is not None:
            assignments.append("target_id=?")
            values.append(target_id)
        values.append(step_id)
        self.connection.execute(
            f"UPDATE proposal_steps SET {', '.join(assignments)} WHERE step_id=?", values
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
