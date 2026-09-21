from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reconciliation.db import SnapshotStore
from reconciliation.executor import ProposalExecutor
from reconciliation.matcher import match_facilities
from reconciliation.models import Account, Contact, Facility
from reconciliation.proposals import build_proposals


PARENT = "parent"
CARE = {"Assisted Living": "Assisted Living"}


def facility() -> Facility:
    return Facility(
        operator_key="test", name="Bellhaven Test", street="100 Main St", city="Testville",
        state="OH", zip_code="44000", care_offerings=("Assisted Living",),
        source_url="https://example.test/facility", phone="(555) 100-0000",
    )


def account(*, revenue: float = 0, ar: float = 0) -> Account:
    return Account.from_api({
        "account_id": "old", "name": "Bellhaven Test", "parent_id": "former",
        "parent_name": "Former Parent", "billing_street": "100 Main St",
        "billing_city": "Testville", "billing_state": "OH", "billing_zip": "44000",
        "care_type": "Assisted Living", "status": "Active", "phone": "(555) 100-0000",
        "lifetime_revenue": revenue, "outstanding_ar": ar, "chow_current_account": "",
        "duplicate_of_account": "", "note": "", "created_by_candidate": False,
        "updated_at": "2026-01-01T00:00:00Z",
    })


class FakeCrm:
    def __init__(self, source: Account, *, fail_update_once: bool = False) -> None:
        self.accounts = {source.account_id: dict(source.raw)}
        self.contacts: dict[str, dict[str, object]] = {}
        self.fail_update_once = fail_update_once
        self.create_calls = 0
        self.update_calls = 0

    def get_account(self, account_id: str) -> Account:
        return Account.from_api(dict(self.accounts[account_id]))

    def get_contact(self, contact_id: str) -> Contact:
        return Contact.from_api(dict(self.contacts[contact_id]))

    def fetch_accounts(self) -> list[Account]:
        return [Account.from_api(dict(item)) for item in self.accounts.values()]

    def create_account(self, payload: dict[str, object]):
        self.create_calls += 1
        created = {**payload, "account_id": f"new-{self.create_calls}", "parent_name": "Bellhaven"}
        self.accounts[created["account_id"]] = created
        return 201, dict(created)

    def update_account(self, account_id: str, payload: dict[str, object]):
        self.update_calls += 1
        if self.fail_update_once:
            self.fail_update_once = False
            raise RuntimeError("simulated transient failure")
        self.accounts[account_id].update(payload)
        return 200, dict(self.accounts[account_id])

    def update_contact(self, contact_id: str, payload: dict[str, object]):
        self.contacts[contact_id].update(payload)
        return 200, dict(self.contacts[contact_id])


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = SnapshotStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def seed(self, source: Account) -> int:
        result = match_facilities([facility()], [source], PARENT)
        proposal = build_proposals("test", PARENT, CARE, result)[0]
        with self.store.run("test") as run_id:
            self.store.save_proposals(run_id, [proposal])
        return int(self.store.connection.execute("SELECT proposal_id FROM proposals").fetchone()[0])

    def approve(self, proposal_id: int) -> None:
        self.store.decide_proposal(proposal_id, "Approved", "Reviewer", "Evidence checked")

    def test_pending_proposal_cannot_execute(self) -> None:
        source = account()
        proposal_id = self.seed(source)
        with self.assertRaises(ValueError):
            ProposalExecutor(self.store, FakeCrm(source)).execute(proposal_id)

    def test_approved_direct_update_is_applied(self) -> None:
        source = account()
        proposal_id = self.seed(source)
        self.approve(proposal_id)
        crm = FakeCrm(source)
        self.assertEqual(ProposalExecutor(self.store, crm).execute(proposal_id), "Applied")
        self.assertEqual(crm.accounts["old"]["parent_id"], PARENT)
        saved = self.store.get_proposal(proposal_id)
        self.assertEqual(saved["status"], "Applied")
        self.assertEqual(saved["steps"][0]["attempts"], 1)

    def test_live_state_drift_blocks_write(self) -> None:
        source = account()
        proposal_id = self.seed(source)
        self.approve(proposal_id)
        crm = FakeCrm(source)
        crm.accounts["old"]["outstanding_ar"] = 99
        self.assertEqual(ProposalExecutor(self.store, crm).execute(proposal_id), "Conflict")
        self.assertEqual(crm.update_calls, 0)

    def test_partial_chow_resumes_without_second_create(self) -> None:
        source = account(revenue=100, ar=25)
        proposal_id = self.seed(source)
        self.approve(proposal_id)
        crm = FakeCrm(source, fail_update_once=True)
        executor = ProposalExecutor(self.store, crm)
        self.assertEqual(executor.execute(proposal_id), "Partially Applied")
        after_failure = self.store.get_proposal(proposal_id)
        self.assertEqual(after_failure["steps"][0]["status"], "Applied")
        self.assertEqual(after_failure["steps"][0]["target_id"], "new-1")
        self.assertEqual(executor.execute(proposal_id), "Applied")
        self.assertEqual(crm.create_calls, 1)
        self.assertEqual(crm.accounts["old"]["chow_current_account"], "new-1")


if __name__ == "__main__":
    unittest.main()
