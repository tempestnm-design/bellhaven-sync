"""Convert match results into immutable, evidence-backed logical changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from . import normalize
from .models import Account, Contact, Facility, MatchResult, Proposal, ProposalStep
from .rules import duplicate_has_financial_risk, parent_change_action, stale_status


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _account_view(account: Account) -> dict[str, Any]:
    return dict(account.raw)


def _desired_account(
    facility: Facility, parent_id: str, care_map: dict[str, str],
    existing: Account | None = None, *, include_phone: bool = False,
) -> dict[str, Any]:
    mapped_care = [care_map[item] for item in facility.care_offerings if item in care_map]
    care_type = existing.care_type if existing and existing.care_type in mapped_care else (mapped_care[0] if mapped_care else "")
    desired = {
        "name": facility.name,
        "parent_id": parent_id,
        "billing_street": facility.street,
        "billing_city": facility.city,
        "billing_state": facility.state,
        "billing_zip": facility.zip_code,
        "care_type": care_type,
        "status": "Active",
    }
    if include_phone and facility.phone:
        desired["phone"] = facility.phone
    return desired


def _changes(account: Account, desired: dict[str, Any]) -> dict[str, Any]:
    current = account.raw
    comparators = {
        "name": normalize.name,
        "billing_street": normalize.address,
        "billing_city": normalize.text,
        "billing_state": normalize.text,
        "billing_zip": normalize.zip_code,
        "phone": normalize.phone,
    }
    changes: dict[str, Any] = {}
    for key, value in desired.items():
        before = str(current.get(key, ""))
        after = str(value)
        comparator = comparators.get(key)
        equal = comparator(before) == comparator(after) if comparator else before == after
        if not equal:
            changes[key] = value
    return changes


def _fingerprint(
    operator_key: str, facility_key: str, classification: str,
    current: list[dict[str, Any]], desired: dict[str, Any], steps: tuple[ProposalStep, ...],
) -> str:
    value = {
        "operator_key": operator_key, "facility_key": facility_key,
        "classification": classification, "current": current,
        "desired": desired, "steps": [step.to_dict() for step in steps],
    }
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _proposal(
    operator_key: str, facility_key: str, classification: str, confidence: str,
    evidence: dict[str, Any], current: list[dict[str, Any]], desired: dict[str, Any],
    steps: tuple[ProposalStep, ...], writable: bool = True,
) -> Proposal:
    return Proposal(
        _fingerprint(operator_key, facility_key, classification, current, desired, steps),
        facility_key, classification, confidence, evidence, current, desired, steps, writable,
    )


def build_proposals(
    operator_key: str, parent_id: str, care_map: dict[str, str], results: list[MatchResult],
    contacts: list[Contact] | None = None,
) -> list[Proposal]:
    proposals: list[Proposal] = []
    contacts = contacts or []
    for result in results:
        evidence = {
            "explanation": result.explanation,
            "candidates": [item.to_dict() for item in result.candidates],
            "website": result.facility.to_dict() if result.facility else None,
            "contacts": [item.raw for item in contacts if item.account_id in {account.account_id for account in result.selected_accounts}],
        }
        if result.classification == "ambiguous_match":
            facility = result.facility
            key = normalize.facility_key(facility.street, facility.city, facility.state, facility.zip_code) if facility else "unknown"
            evidence["recommendation"] = "Review manually — the evidence is not strong enough to authorize a CRM change."
            proposals.append(_proposal(
                operator_key, key, "ambiguous_match", result.confidence, evidence,
                [_account_view(item) for item in result.selected_accounts], {}, (), False,
            ))
            continue

        if result.classification == "missing_account":
            facility = result.facility
            assert facility is not None
            desired = _desired_account(facility, parent_id, care_map, include_phone=True)
            evidence["recommendation"] = f'Create a new Active CRM account for “{facility.name}” under the configured parent.'
            key = normalize.facility_key(facility.street, facility.city, facility.state, facility.zip_code)
            steps = (ProposalStep(1, "POST account", "", desired),)
            proposals.append(_proposal(operator_key, key, "create_account", result.confidence, evidence, [], desired, steps))
            continue

        if result.classification == "stale_parent_child":
            account = result.selected_accounts[0]
            status = stale_status(account)
            risky = status == "Needs Review"
            reconciliation_note = (
                "Absent from the configured operator's complete current website inventory; "
                + ("ownership/billing review required because outstanding AR remains." if risky else "inactivated during ownership reconciliation.")
            )
            existing_note = account.note.strip()
            desired = {
                "status": status,
                "note": existing_note if reconciliation_note in existing_note else "\n".join(
                    item for item in (existing_note, reconciliation_note) if item
                ),
            }
            changes = _changes(account, desired)
            if changes:
                evidence["recommendation"] = (
                    f'Place “{account.name}” in Needs Review without changing its parent; outstanding AR requires investigation.'
                    if risky else
                    f'Mark “{account.name}” Inactive because it is absent from the complete website inventory and has no outstanding AR.'
                )
                steps = (ProposalStep(1, "PATCH account", account.account_id, changes),)
                proposals.append(_proposal(
                    operator_key, f"stale|{account.account_id}",
                    "needs_review" if risky else "inactivate_stale", result.confidence,
                    evidence, [_account_view(account)], desired, steps,
                ))
            continue

        facility = result.facility
        assert facility is not None
        key = normalize.facility_key(facility.street, facility.city, facility.state, facility.zip_code)
        existing = result.selected_accounts[0] if result.selected_accounts else None
        desired = _desired_account(facility, parent_id, care_map, existing)

        if result.classification == "duplicate_group":
            survivor, *losers = result.selected_accounts
            if duplicate_has_financial_risk(losers):
                evidence["recommendation"] = "Review manually — a proposed duplicate loser has financial history that makes automated consolidation unsafe."
                proposals.append(_proposal(
                    operator_key, key, "ambiguous_duplicate_financial_risk", "low", evidence,
                    [_account_view(item) for item in result.selected_accounts], desired, (), False,
                ))
                continue
            steps_list: list[ProposalStep] = []
            survivor_changes = _changes(survivor, desired)
            if survivor_changes:
                steps_list.append(ProposalStep(len(steps_list) + 1, "PATCH account", survivor.account_id, survivor_changes))
            for loser in losers:
                for contact in contacts:
                    if contact.account_id == loser.account_id and contact.is_active:
                        steps_list.append(ProposalStep(
                            len(steps_list) + 1, "PATCH contact", contact.contact_id,
                            {"account_id": survivor.account_id},
                        ))
                loser_changes = _changes(
                    loser,
                    {"duplicate_of_account": survivor.account_id, "status": "Inactive"},
                )
                if loser_changes:
                    steps_list.append(ProposalStep(
                        len(steps_list) + 1, "PATCH account", loser.account_id,
                        loser_changes,
                    ))
            steps = tuple(steps_list)
            if not steps:
                # The duplicate remains useful matching evidence, but its CRM
                # survivor/loser state and contact ownership are already final.
                continue
            loser_names = ", ".join(f'“{item.name}”' for item in losers)
            evidence["recommendation"] = (
                f'Keep “{survivor.name}” (account {survivor.account_id}) as the survivor; move active contacts '
                f'and mark {loser_names} as inactive duplicate record(s).'
            )
            proposals.append(_proposal(
                operator_key, key, "resolve_duplicates", result.confidence, evidence,
                [_account_view(item) for item in result.selected_accounts], desired, steps,
            ))
            continue

        account = result.selected_accounts[0]
        changes = _changes(account, desired)
        if not changes:
            continue
        parent_action = parent_change_action(account, parent_id)
        if parent_action == "chow":
            create_request = _desired_account(facility, parent_id, care_map, include_phone=True)
            steps = (
                ProposalStep(1, "POST account", "", create_request),
                ProposalStep(2, "PATCH account", account.account_id, {"chow_current_account": "$step_1.account_id"}),
            )
            classification = "chow"
            evidence["recommendation"] = (
                f'Create a new account for “{facility.name}” under the configured parent and link the old account to it as a CHOW. '
                "Do not re-parent the historical account because it has both revenue history and outstanding AR."
            )
        else:
            steps = (ProposalStep(1, "PATCH account", account.account_id, changes),)
            classification = "reparent_or_correct" if parent_action == "direct" else "correct_fields"
            fields = ", ".join(key.replace("billing_", "").replace("_", " ") for key in changes)
            evidence["recommendation"] = f'Update “{account.name}” in place: {fields}.'
        proposals.append(_proposal(
            operator_key, key, classification, result.confidence, evidence,
            [_account_view(account)], desired, steps,
        ))
    return proposals
