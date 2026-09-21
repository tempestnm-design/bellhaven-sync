"""Pure business-policy decisions, independent of matching and persistence."""

from __future__ import annotations

from .models import Account


def parent_change_action(account: Account, parent_id: str) -> str:
    if account.parent_id == parent_id:
        return "none"
    if account.lifetime_revenue > 0 and account.outstanding_ar > 0:
        return "chow"
    return "direct"


def stale_status(account: Account) -> str:
    return "Needs Review" if account.outstanding_ar > 0 else "Inactive"


def duplicate_has_financial_risk(losers: list[Account]) -> bool:
    return any(account.outstanding_ar > 0 for account in losers)
