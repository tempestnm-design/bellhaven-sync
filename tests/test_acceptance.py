"""Assessment-shape oracle: production rules must derive these counts."""

from __future__ import annotations

import unittest
from collections import Counter

from reconciliation.matcher import match_facilities
from reconciliation.models import Account, Facility
from reconciliation.proposals import build_proposals


PARENT = "bellhaven-parent"
CARE = {"Assisted Living": "Assisted Living"}


def make_facility(index: int, name: str | None = None) -> Facility:
    return Facility(
        operator_key="oracle", name=name or f"Bellhaven {index}", street=f"{100 + index} Main St",
        city=f"City {index}", state="OH", zip_code=f"{44000 + index:05d}",
        care_offerings=("Assisted Living",), source_url=f"https://example.test/{index}",
        phone=f"(555) 100-{index:04d}",
    )


def make_account(
    account_id: str, facility: Facility, *, parent_id: str = PARENT,
    name: str | None = None, phone: str | None = None, revenue: float = 0,
    ar: float = 0,
) -> Account:
    raw = {
        "account_id": account_id, "name": name or facility.name, "parent_id": parent_id,
        "parent_name": "Bellhaven", "billing_street": facility.street,
        "billing_city": facility.city, "billing_state": facility.state,
        "billing_zip": facility.zip_code, "care_type": "Assisted Living",
        "status": "Active", "phone": phone if phone is not None else facility.phone,
        "lifetime_revenue": revenue, "outstanding_ar": ar,
        "chow_current_account": "", "duplicate_of_account": "", "note": "",
        "created_by_candidate": False, "updated_at": "2026-01-01T00:00:00Z",
    }
    return Account.from_api(raw)


class AssessmentOracleTests(unittest.TestCase):
    def test_derives_assessment_population_and_rule_outcomes(self) -> None:
        facilities = [make_facility(index) for index in range(35)]
        accounts: list[Account] = []

        # 25 website facilities already represented by distinct Bellhaven children.
        accounts.extend(make_account(f"child-{index}", facilities[index]) for index in range(25))
        # One of those facilities has a second Bellhaven copy; website phone selects survivor.
        accounts.append(make_account(
            "child-duplicate", facilities[0], phone="(555) 999-9999"
        ))

        # Six website facilities have existing records outside the parent.
        for index in range(25, 28):
            accounts.append(make_account(f"direct-{index}", facilities[index], parent_id="former"))
        for index in range(28, 30):
            accounts.append(make_account(
                f"chow-{index}", facilities[index], parent_id="former", revenue=100, ar=25
            ))

        # Kettering-shaped case: three prior/unparented copies and no current-parent child.
        kettering = facilities[30]
        accounts.extend([
            make_account("kettering-unparented", kettering, parent_id="", name="City Nursing & Rehabilitation"),
            make_account("kettering-harborview", kettering, parent_id="harborview", name="City Care Centre"),
            make_account("kettering-cedar", kettering, parent_id="cedar", name="City Senior Campus"),
        ])

        # Three Bellhaven children are absent from the complete website inventory.
        stale_safe = make_facility(100, "Stale Safe")
        stale_safe_two = make_facility(101, "Stale Safe Two")
        stale_risky = make_facility(102, "Stale Risky")
        accounts.extend([
            make_account("stale-safe", stale_safe),
            make_account("stale-safe-two", stale_safe_two),
            make_account("stale-risky", stale_risky, revenue=100, ar=10),
        ])

        results = match_facilities(facilities, accounts, PARENT)
        self.assertEqual(len(facilities), 35)
        self.assertEqual(sum(item.parent_id == PARENT for item in accounts), 29)
        self.assertEqual(len(results), 38)  # 35 website results + 3 stale children
        self.assertEqual(Counter(item.classification for item in results), {
            "confident_match": 29, "duplicate_group": 2,
            "missing_account": 4, "stale_parent_child": 3,
        })

        proposals = build_proposals("oracle", PARENT, CARE, results)
        classifications = Counter(item.classification for item in proposals)
        self.assertEqual(classifications["create_account"], 4)
        self.assertEqual(classifications["reparent_or_correct"], 3)
        self.assertEqual(classifications["chow"], 2)
        self.assertEqual(classifications["resolve_duplicates"], 2)
        self.assertEqual(classifications["inactivate_stale"], 2)
        self.assertEqual(classifications["needs_review"], 1)

        kettering_proposal = next(
            item for item in proposals
            if item.classification == "resolve_duplicates"
            and item.facility_key.startswith("130 main st")
        )
        self.assertEqual(kettering_proposal.steps[0].target_id, "kettering-unparented")


if __name__ == "__main__":
    unittest.main()
