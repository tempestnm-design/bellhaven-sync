"""Read-only CRM API client used by ingestion."""

from __future__ import annotations

from urllib.parse import urlencode

from .errors import ValidationError
from .http import HttpClient
from .models import Account, Contact, OperatorProfile


class CrmClient:
    def __init__(self, profile: OperatorProfile, token: str, http: HttpClient | None = None) -> None:
        self.profile = profile
        self.http = http or HttpClient()
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def _get_json(self, path: str, params: dict[str, object] | None = None) -> object:
        url = self.profile.crm_api_base_url + path
        if params:
            url += "?" + urlencode(params)
        return self.http.get(url, self.headers).json()

    def _write_json(
        self, method: str, path: str, payload: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        response = self.http.request(
            method, self.profile.crm_api_base_url + path,
            headers=self.headers, json_body=payload,
        )
        parsed = response.json()
        if not isinstance(parsed, dict):
            raise ValidationError(f"CRM {method} response was not an object")
        return response.status, parsed

    def verify_workspace(self) -> dict[str, object]:
        payload = self._get_json("/me")
        if not isinstance(payload, dict):
            raise ValidationError("CRM /me response was not an object")
        return payload

    def fetch_accounts(self, *, page_size: int = 100) -> list[Account]:
        rows: list[dict[str, object]] = []
        page = 1
        while True:
            payload = self._get_json("/accounts", {"page": page, "page_size": page_size})
            if isinstance(payload, list):
                batch, total = payload, None
            elif isinstance(payload, dict):
                batch = next(
                    (payload[key] for key in ("accounts", "items", "results", "data") if isinstance(payload.get(key), list)),
                    None,
                )
                total = payload.get("total")
                if batch is None:
                    raise ValidationError("CRM accounts response has no recognized row list")
            else:
                raise ValidationError("CRM accounts response was not a list or object")
            rows.extend(item for item in batch if isinstance(item, dict))
            if not batch or (total is not None and len(rows) >= int(total)) or len(batch) < page_size:
                break
            page += 1
        accounts = [Account.from_api(row) for row in rows]
        ids = [account.account_id for account in accounts]
        if any(not account_id for account_id in ids) or len(ids) != len(set(ids)):
            raise ValidationError("CRM snapshot contains blank or duplicate account IDs")
        return accounts

    def validate_parent(self, accounts: list[Account]) -> Account:
        matches = [account for account in accounts if account.name == self.profile.parent_account_name]
        if len(matches) != 1:
            raise ValidationError(
                f"Expected one {self.profile.parent_account_name!r} parent; found {len(matches)}"
            )
        parent = matches[0]
        expected = self.profile.expected_parent_account_id
        if expected and parent.account_id != expected:
            raise ValidationError(f"Parent ID changed: expected {expected}, got {parent.account_id}")
        return parent

    def fetch_contacts(self, account_id: str, *, page_size: int = 100) -> list[Contact]:
        rows: list[dict[str, object]] = []
        page = 1
        while True:
            payload = self._get_json(
                "/contacts", {"account_id": account_id, "page": page, "page_size": page_size}
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValidationError("CRM contacts response has no data list")
            batch = payload["data"]
            rows.extend(item for item in batch if isinstance(item, dict))
            if not batch or len(rows) >= int(payload.get("total", len(rows))):
                break
            page += 1
        contacts = [Contact.from_api(row) for row in rows]
        if any(not item.contact_id for item in contacts):
            raise ValidationError("CRM contact snapshot contains a blank contact ID")
        return contacts

    def get_account(self, account_id: str) -> Account:
        payload = self._get_json(f"/accounts/{account_id}")
        if not isinstance(payload, dict):
            raise ValidationError("CRM account response was not an object")
        return Account.from_api(payload)

    def get_contact(self, contact_id: str) -> Contact:
        payload = self._get_json(f"/contacts/{contact_id}")
        if not isinstance(payload, dict):
            raise ValidationError("CRM contact response was not an object")
        return Contact.from_api(payload)

    def create_account(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        return self._write_json("POST", "/accounts", payload)

    def update_account(
        self, account_id: str, payload: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        return self._write_json("PATCH", f"/accounts/{account_id}", payload)

    def update_contact(
        self, contact_id: str, payload: dict[str, object]
    ) -> tuple[int, dict[str, object]]:
        return self._write_json("PATCH", f"/contacts/{contact_id}", payload)
