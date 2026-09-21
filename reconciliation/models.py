"""Domain models. Raw source values are preserved for audit evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OperatorProfile:
    operator_key: str
    display_name: str
    website_base_url: str
    directory_path: str
    crm_api_base_url: str
    token_environment_variable: str
    parent_account_name: str
    expected_parent_account_id: str | None
    minimum_facilities: int
    maximum_facilities: int
    required_directory_pages: int | None
    care_type_map: dict[str, str]


@dataclass(frozen=True)
class Facility:
    operator_key: str
    name: str
    street: str
    city: str
    state: str
    zip_code: str
    care_offerings: tuple[str, ...]
    source_url: str
    administrator: str = ""
    phone: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["care_offerings"] = list(self.care_offerings)
        return data


@dataclass(frozen=True)
class Account:
    account_id: str
    name: str
    parent_id: str
    parent_name: str
    billing_street: str
    billing_city: str
    billing_state: str
    billing_zip: str
    care_type: str
    status: str
    phone: str
    lifetime_revenue: float
    outstanding_ar: float
    chow_current_account: str
    duplicate_of_account: str
    note: str
    created_by_candidate: bool
    updated_at: str
    raw: dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Account":
        return cls(
            account_id=str(raw.get("account_id", "")),
            name=str(raw.get("name", "")),
            parent_id=str(raw.get("parent_id", "")),
            parent_name=str(raw.get("parent_name", "")),
            billing_street=str(raw.get("billing_street", "")),
            billing_city=str(raw.get("billing_city", "")),
            billing_state=str(raw.get("billing_state", "")),
            billing_zip=str(raw.get("billing_zip", "")),
            care_type=str(raw.get("care_type", "")),
            status=str(raw.get("status", "")),
            phone=str(raw.get("phone", "")),
            lifetime_revenue=float(raw.get("lifetime_revenue", 0) or 0),
            outstanding_ar=float(raw.get("outstanding_ar", 0) or 0),
            chow_current_account=str(raw.get("chow_current_account", "")),
            duplicate_of_account=str(raw.get("duplicate_of_account", "")),
            note=str(raw.get("note", "")),
            created_by_candidate=bool(raw.get("created_by_candidate", False)),
            updated_at=str(raw.get("updated_at", "")),
            raw=dict(raw),
        )
