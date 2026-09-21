from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from reconciliation.db import SnapshotStore
from reconciliation.matcher import match_facilities
from reconciliation.models import Account, Facility
from reconciliation.proposals import build_proposals


class ReviewAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "review.sqlite3"
        store = SnapshotStore(self.path)
        facility = Facility(
            operator_key="test", name="Bellhaven Test", street="100 Main St",
            city="Testville", state="OH", zip_code="44000",
            care_offerings=("Assisted Living",), source_url="https://example.test/facility",
        )
        account = Account.from_api({
            "account_id": "old", "name": "Bellhaven Test", "parent_id": "former",
            "parent_name": "Former", "billing_street": "100 Main St",
            "billing_city": "Testville", "billing_state": "OH", "billing_zip": "44000",
            "care_type": "Assisted Living", "status": "Active", "phone": "",
            "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "",
            "duplicate_of_account": "", "note": "", "created_by_candidate": False,
        })
        proposal = build_proposals(
            "test", "parent", {"Assisted Living": "Assisted Living"},
            match_facilities([facility], [account], "parent"),
        )[0]
        with store.run("test") as run_id:
            store.save_proposals(run_id, [proposal])
        self.proposal_id = int(store.connection.execute("SELECT proposal_id FROM proposals").fetchone()[0])
        store.close()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "test-secret", "DATABASE": str(self.path),
            "REVIEWER_NAME": "Test Reviewer",
        })
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def token(self) -> str:
        self.client.get("/")
        with self.client.session_transaction() as session:
            return str(session["csrf_token"])

    def test_queue_and_detail_render_evidence(self) -> None:
        queue = self.client.get("/")
        self.assertEqual(queue.status_code, 200)
        self.assertIn(b"Bellhaven Test", queue.data)
        detail = self.client.get(f"/proposals/{self.proposal_id}")
        self.assertIn(b"Ordered operations", detail.data)
        self.assertIn(b"Approve without executing", detail.data)

    def test_approval_records_decision_without_execution(self) -> None:
        response = self.client.post(
            f"/proposals/{self.proposal_id}/approve",
            data={
                "csrf_token": self.token(), "reviewer_name": "Neil",
                "reason": "Verified address and CHOW fields",
            },
            follow_redirects=True,
        )
        self.assertIn(b"No CRM write has occurred yet", response.data)
        store = SnapshotStore(self.path)
        proposal = store.get_proposal(self.proposal_id)
        self.assertEqual(proposal["status"], "Approved")
        self.assertEqual(proposal["reviewer_name"], "Neil")
        self.assertEqual(len(proposal["audit_events"]), 1)
        self.assertTrue(all(step["status"] == "Pending" for step in proposal["steps"]))
        store.close()

    def test_rejection_requires_reason_and_csrf(self) -> None:
        missing_csrf = self.client.post(f"/proposals/{self.proposal_id}/reject", data={"reason": "No"})
        self.assertEqual(missing_csrf.status_code, 400)
        missing_reason = self.client.post(
            f"/proposals/{self.proposal_id}/reject",
            data={"csrf_token": self.token(), "reviewer_name": "Neil", "reason": ""},
            follow_redirects=True,
        )
        self.assertIn(b"rejection reason is required", missing_reason.data)


if __name__ == "__main__":
    unittest.main()
