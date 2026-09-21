"""Deterministic candidate generation and explainable facility matching."""

from __future__ import annotations

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
    return CandidateEvidence(account.account_id, score, tuple(signals), tuple(contradictions))


def _survivor_score(facility: Facility, account: Account, parent_id: str) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []
    similarity = normalize.name_similarity(facility.name, account.name)
    score += similarity * 40
    reasons.append(f"name similarity {similarity:.2f}")
    if account.parent_id == parent_id:
        score += 25
        reasons.append("already under configured parent")
    elif not account.parent_id:
        score += 15
        reasons.append("unparented record avoids overwriting historical ownership")
    if normalize.phone(facility.phone) and normalize.phone(facility.phone) == normalize.phone(account.phone):
        score += 20
        reasons.append("website phone matches")
    if account.status == "Active":
        score += 5
    return score, "; ".join(reasons)


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
                    "Multiple historical records share the address; the sole configured-parent child is current.",
                ))
                continue
            if len(current_parent) > 1:
                group = current_parent
            ranked = sorted(
                ((_survivor_score(facility, account, parent_id), account) for account in group),
                key=lambda item: (-item[0][0], item[1].account_id),
            )
            selected = tuple(item[1] for item in ranked)
            gap = ranked[0][0][0] - ranked[1][0][0]
            if gap < 5:
                results.append(MatchResult(
                    facility, "ambiguous_match", "low", selected, tuple(evidence),
                    "Multiple records share the physical identity and no defensible survivor leads by five points.",
                ))
            else:
                results.append(MatchResult(
                    facility, "duplicate_group", "high" if gap >= 15 else "medium",
                    selected, tuple(evidence),
                    f"Multiple records share the physical identity; survivor score lead is {gap:.1f}.",
                ))
        elif evidence and evidence[0].score >= 80:
            selected = account_by_id[evidence[0].account_id]
            confidence = "high" if evidence[0].score >= 95 else "medium"
            results.append(MatchResult(
                facility, "confident_match", confidence, (selected,), tuple(evidence),
                f"Selected {selected.account_id} from deterministic location evidence.",
            ))
        elif evidence:
            results.append(MatchResult(
                facility, "ambiguous_match", "low", (), tuple(evidence),
                "Candidate evidence did not meet the writable match threshold.",
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
                "Configured-parent child is absent from the complete website inventory.",
            ))
    return results
