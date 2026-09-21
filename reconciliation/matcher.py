"""Deterministic candidate generation and explainable facility matching."""

from __future__ import annotations

from dataclasses import replace

from . import normalize
from .models import Account, CandidateEvidence, Facility, MatchResult


def _candidate(facility: Facility, account: Account) -> CandidateEvidence | None:
    signals: list[str] = []
    contradictions: list[str] = []
    score = 0.0
    street_equal = normalize.address(facility.street) == normalize.address(account.billing_street)
    city_equal = normalize.text(facility.city) == normalize.text(account.billing_city)
    state_equal = normalize.text(facility.state) == normalize.text(account.billing_state)
    zip_equal = normalize.zip_code(facility.zip_code) == normalize.zip_code(account.billing_zip)
    name_sim = normalize.name_similarity(facility.name, account.name)

    if street_equal and city_equal and state_equal:
        score, signals = 100.0, ["exact normalized street + city + state"]
    elif street_equal and zip_equal:
        score, signals = 95.0, ["exact normalized street + ZIP"]
    elif normalize.name(facility.name) == normalize.name(account.name) and city_equal and state_equal:
        score, signals = 88.0, ["exact normalized name + city + state"]
    elif city_equal and state_equal and zip_equal and name_sim >= 0.62:
        score = 60.0 + (name_sim * 30.0)
        signals = [f"city + state + ZIP; name similarity {name_sim:.2f}"]
    else:
        return None

    if normalize.phone(facility.phone) and normalize.phone(facility.phone) == normalize.phone(account.phone):
        score += 8.0
        signals.append("exact normalized phone")
    if not zip_equal:
        contradictions.append("ZIP differs")
    if not city_equal or not state_equal:
        contradictions.append("city/state differs")
    location = ", ".join(filter(None, (
        account.billing_street,
        f"{account.billing_city}, {account.billing_state} {account.billing_zip}".strip(),
    )))
    return CandidateEvidence(
        account.account_id, score, tuple(signals), tuple(contradictions),
        account_name=account.name, parent_name=account.parent_name or "Unparented",
        location=location, status=account.status,
    )


def _survivor_score(facility: Facility, account: Account, parent_id: str) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    reasons: list[str] = []
    similarity = normalize.name_similarity(facility.name, account.name)
    name_points = similarity * 40
    score += name_points
    reasons.append(f"Name similarity {similarity:.2f}: +{name_points:.1f}/40")
    if account.parent_id == parent_id:
        score += 25
        reasons.append("Already under the configured parent: +25/25")
    elif not account.parent_id:
        score += 15
        reasons.append("Unparented record preserves historical ownership: +15/25")
    else:
        reasons.append("Different historical parent: +0/25")
    if normalize.phone(facility.phone) and normalize.phone(facility.phone) == normalize.phone(account.phone):
        score += 20
        reasons.append("Website phone matches: +20/20")
    else:
        reasons.append("Website phone does not match: +0/20")
    if account.status == "Active":
        score += 5
        reasons.append("Account is Active: +5/5")
    else:
        reasons.append("Account is not Active: +0/5")
    return score, tuple(reasons)


def _account_label(account: Account) -> str:
    parent = account.parent_name or "Unparented"
    return f'“{account.name}” ({parent}; account {account.account_id})'


def match_facilities(
    facilities: list[Facility], accounts: list[Account], parent_id: str
) -> list[MatchResult]:
    results: list[MatchResult] = []
    for facility in facilities:
        evidence = sorted(
            (item for account in accounts if (item := _candidate(facility, account))),
            key=lambda item: (-item.score, item.account_id),
        )
        strong = [item for item in evidence if item.score >= 95]
        account_by_id = {account.account_id: account for account in accounts}
        if len(strong) > 1:
            group = [account_by_id[item.account_id] for item in strong]
            current_parent = [account for account in group if account.parent_id == parent_id]
            if len(current_parent) == 1:
                selected = current_parent[0]
                results.append(MatchResult(
                    facility, "confident_match", "high", (selected,), tuple(evidence),
                    f"Selected {_account_label(selected)} because it is the only same-address record "
                    "already under the configured parent.",
                ))
                continue
            if len(current_parent) > 1:
                group = current_parent
            ranked = sorted(
                ((_survivor_score(facility, account, parent_id), account) for account in group),
                key=lambda item: (-item[0][0], item[1].account_id),
            )
            survivor_by_id = {account.account_id: outcome for outcome, account in ranked}
            evidence = [
                replace(
                    item,
                    survivor_score=survivor_by_id[item.account_id][0],
                    survivor_signals=survivor_by_id[item.account_id][1],
                ) if item.account_id in survivor_by_id else item
                for item in evidence
            ]
            selected = tuple(item[1] for item in ranked)
            gap = ranked[0][0][0] - ranked[1][0][0]
            if gap < 5:
                results.append(MatchResult(
                    facility, "ambiguous_match", "low", selected, tuple(evidence),
                    f"Same-address records are too close to choose safely: {_account_label(ranked[0][1])} "
                    f"scores {ranked[0][0][0]:.1f}/90, only {gap:.1f} points ahead of "
                    f"{_account_label(ranked[1][1])}. No CRM write is proposed.",
                ))
            else:
                results.append(MatchResult(
                    facility, "duplicate_group", "high" if gap >= 15 else "medium",
                    selected, tuple(evidence),
                    f"Recommend {_account_label(ranked[0][1])} as the surviving record. Its "
                    f"survivor score is {ranked[0][0][0]:.1f}/90, {gap:.1f} points ahead of "
                    f"{_account_label(ranked[1][1])}. The score combines name fit (40), "
                    "ownership fit (25), phone match (20), and active status (5).",
                ))
        elif evidence and evidence[0].score >= 80:
            selected = account_by_id[evidence[0].account_id]
            confidence = "high" if evidence[0].score >= 95 else "medium"
            results.append(MatchResult(
                facility, "confident_match", confidence, (selected,), tuple(evidence),
                f"Selected {_account_label(selected)} from deterministic location evidence "
                f"(match score {evidence[0].score:.1f}/108; writable threshold 80).",
            ))
        elif evidence:
            results.append(MatchResult(
                facility, "ambiguous_match", "low", (), tuple(evidence),
                f"The best candidate scored {evidence[0].score:.1f}/108, below the writable "
                "threshold of 80. No CRM write is proposed.",
            ))
        else:
            results.append(MatchResult(
                facility, "missing_account", "high", (), (),
                "No defensible candidate was found in the complete CRM population.",
            ))

    represented = {
        account.account_id
        for result in results
        if result.classification != "ambiguous_match"
        for account in result.selected_accounts
    }
    for account in accounts:
        if account.parent_id == parent_id and account.account_id not in represented:
            results.append(MatchResult(
                None, "stale_parent_child", "high", (account,), (),
                (
                    f"{_account_label(account)} is absent from all current website communities but has "
                    f"${account.lifetime_revenue:,.2f} lifetime revenue and "
                    f"${account.outstanding_ar:,.2f} outstanding AR. Recommend Needs Review; preserve "
                    "the account and its parent while billing or ownership is investigated."
                    if account.outstanding_ar > 0 else
                    f"{_account_label(account)} is absent from all current website communities and has "
                    "$0.00 outstanding AR. Recommend marking it Inactive with an ownership-reconciliation note."
                ),
            ))
    return results
