from __future__ import annotations

import unittest
import tempfile
from dataclasses import replace
from pathlib import Path

from reconciliation.db import SnapshotStore
from reconciliation.matcher import match_facilities
from reconciliation.models import Account, Contact, Facility
from reconciliation.proposals import build_proposals


PARENT = "parent"
CARE_MAP = {"Assisted Living": "Assisted Living", "Memory Support": "Memory Care"}


def facility(**overrides: object) -> Facility:
    values = dict(
        operator_key="test", name="Bellhaven of Findlay", street="1800 N Blanchard St",
        city="Findlay", state="OH", zip_code="45840",
        care_offerings=("Assisted Living",), source_url="https://example.test/findlay",
        administrator="Sam Pruitt", phone="(231) 533-2969",
    )
    values.update(overrides)
    return Facility(**values)


def account(account_id: str, **overrides: object) -> Account:
    values = dict(
        account_id=account_id, name="Bellhaven of Findlay", parent_id=PARENT,
        parent_name="Bellhaven", billing_street="1800 N Blanchard Street",
        billing_city="Findlay", billing_state="OH", billing_zip="45840",
        care_type="Assisted Living", status="Active", phone="(231) 533-2969",
        lifetime_revenue=0.0, outstanding_ar=0.0, chow_current_account="",
        duplicate_of_account="", note="", created_by_candidate=False,
        updated_at="2026-01-01T00:00:00Z", raw={},
    )
    values.update(overrides)
    raw = {key: value for key, value in values.items() if key not in {"raw"}}
    values["raw"] = raw
    return Account(**values)


class MatcherTests(unittest.TestCase):
    def test_address_match_outranks_branding(self) -> None:
        target = account("target", name="Old Facility Name")
        distractor = account(
            "distractor", name="Bellhaven of Findlay", billing_street="999 Other Rd",
            billing_zip="45841", phone="",
        )
        result = match_facilities([facility()], [distractor, target], PARENT)[0]
        self.assertEqual(result.selected_accounts[0].account_id, "target")
        self.assertEqual(result.confidence, "high")
        self.assertIn("Old Facility Name", result.explanation)
        self.assertIn("/108", result.explanation)

    def test_close_duplicate_survivors_are_nonwritable_ambiguity(self) -> None:
        first = account("a")
        second = account("b")
        result = match_facilities([facility()], [first, second], PARENT)[0]
        self.assertEqual(result.classification, "ambiguous_match")


class ProposalTests(unittest.TestCase):
    def test_parent_change_with_revenue_and_ar_uses_chow(self) -> None:
        old = account("old", parent_id="former", lifetime_revenue=10, outstanding_ar=2)
        results = match_facilities([facility()], [old], PARENT)
        proposals = build_proposals("test", PARENT, CARE_MAP, results)
        self.assertEqual(proposals[0].classification, "chow")
        self.assertEqual([step.operation for step in proposals[0].steps], ["POST account", "PATCH account"])
        self.assertEqual(proposals[0].steps[1].request["chow_current_account"], "$step_1.account_id")

    def test_parent_change_without_ar_is_direct(self) -> None:
        old = account("old", parent_id="former", lifetime_revenue=10, outstanding_ar=0)
        proposal = build_proposals("test", PARENT, CARE_MAP, match_facilities([facility()], [old], PARENT))[0]
        self.assertEqual(proposal.classification, "reparent_or_correct")
        self.assertEqual(proposal.steps[0].operation, "PATCH account")

    def test_stale_open_ar_routes_to_needs_review(self) -> None:
        stale = account(
            "stale", name="Old Child", billing_street="9 Old Rd", billing_city="Alliance",
            billing_zip="44601", outstanding_ar=50,
        )
        proposals = build_proposals("test", PARENT, CARE_MAP, match_facilities([], [stale], PARENT))
        self.assertEqual(proposals[0].classification, "needs_review")
        self.assertEqual(proposals[0].desired["status"], "Needs Review")
        self.assertIn("outstanding AR", proposals[0].evidence["explanation"])
        self.assertIn("Needs Review", proposals[0].evidence["recommendation"])

    def test_stale_without_ar_is_explicitly_inactive(self) -> None:
        stale = account("stale", name="Old Child", billing_street="9 Old Rd", outstanding_ar=0)
        proposal = build_proposals("test", PARENT, CARE_MAP, match_facilities([], [stale], PARENT))[0]
        self.assertEqual(proposal.classification, "inactivate_stale")
        self.assertIn("Inactive", proposal.evidence["recommendation"])

    def test_duplicate_survivor_score_has_scale_and_breakdown(self) -> None:
        survivor = account("survivor", parent_id="", parent_name="", phone="(231) 533-2969")
        loser = account("loser", parent_id="former", parent_name="Former", phone="")
        result = match_facilities([facility()], [survivor, loser], PARENT)[0]
        self.assertEqual(result.classification, "duplicate_group")
        self.assertIn("/90", result.explanation)
        evidence = {item.account_id: item for item in result.candidates}
        self.assertIsNotNone(evidence["survivor"].survivor_score)
        self.assertTrue(any("+20/20" in item for item in evidence["survivor"].survivor_signals))

    def test_fingerprint_is_stable(self) -> None:
        results = match_facilities([facility()], [account("old", parent_id="former")], PARENT)
        first = build_proposals("test", PARENT, CARE_MAP, results)[0]
        second = build_proposals("test", PARENT, CARE_MAP, results)[0]
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_identical_proposal_is_not_queued_twice(self) -> None:
        results = match_facilities([facility()], [account("old", parent_id="former")], PARENT)
        proposal = build_proposals("test", PARENT, CARE_MAP, results)[0]
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory) / "test.sqlite3")
            with store.run("test") as first_run:
                self.assertEqual(store.save_proposals(first_run, [proposal]), 1)
            with store.run("test") as second_run:
                self.assertEqual(store.save_proposals(second_run, [proposal]), 0)
            store.close()

    def test_pending_proposal_refreshes_improved_evidence_without_duplication(self) -> None:
        results = match_facilities([facility()], [account("old", parent_id="former")], PARENT)
        proposal = build_proposals("test", PARENT, CARE_MAP, results)[0]
        improved = replace(proposal, evidence={**proposal.evidence, "explanation": "Clearer rationale"})
        with tempfile.TemporaryDirectory() as directory:
            store = SnapshotStore(Path(directory) / "test.sqlite3")
            with store.run("test") as first_run:
                store.save_proposals(first_run, [proposal])
            with store.run("test") as second_run:
                self.assertEqual(store.save_proposals(second_run, [improved]), 0)
            stored = store.connection.execute(
                "SELECT evidence_json FROM proposals WHERE fingerprint = ?", (proposal.fingerprint,)
            ).fetchone()[0]
            self.assertIn("Clearer rationale", stored)
            store.close()

    def test_duplicate_contact_moves_before_loser_inactivation(self) -> None:
        survivor = account("survivor", phone="(231) 533-2969")
        loser = account("loser", phone="(999) 999-9999")
        result = match_facilities([facility()], [survivor, loser], PARENT)
        contact_raw = {
            "contact_id": "contact", "account_id": "loser", "name": "Admissions Director",
            "title": "Admissions Director", "email": "director@example.test", "phone": "",
            "is_active": True,
        }
        contact = Contact.from_api(contact_raw)
        proposal = build_proposals("test", PARENT, CARE_MAP, result, [contact])[0]
        operations = [(step.operation, step.target_id) for step in proposal.steps]
        self.assertEqual(operations, [("PATCH contact", "contact"), ("PATCH account", "loser")])


if __name__ == "__main__":
    unittest.main()
