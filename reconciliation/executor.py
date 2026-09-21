"""Execute only explicitly approved proposals with live-state conflict checks."""

from __future__ import annotations

from typing import Any

from . import normalize
from .crm import CrmClient
from .db import SnapshotStore
from .errors import IndeterminateWriteError


ACCOUNT_FIELDS = (
    "name", "parent_id", "parent_name", "billing_street", "billing_city",
    "billing_state", "billing_zip", "care_type", "status", "phone",
    "lifetime_revenue", "outstanding_ar", "chow_current_account",
    "duplicate_of_account", "note", "created_by_candidate",
)
CONTACT_FIELDS = (
    "account_id", "name", "title", "email", "phone", "is_active",
    "created_by_candidate",
)


class ProposalExecutor:
    def __init__(self, store: SnapshotStore, crm: CrmClient) -> None:
        self.store = store
        self.crm = crm

    @staticmethod
    def _differences(snapshot: dict[str, Any], live: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
        return [field for field in fields if snapshot.get(field) != live.get(field)]

    def _preflight(self, proposal: dict[str, Any]) -> list[str]:
        conflicts: list[str] = []
        current_accounts = {
            item["account_id"]: item for item in proposal["current"] if item.get("account_id")
        }
        contact_snapshots = {
            item["contact_id"]: item
            for item in proposal["evidence"].get("contacts", [])
            if item.get("contact_id")
        }
        pending = [step for step in proposal["steps"] if step["status"] in {"Pending", "Failed"}]
        if any(step["status"] == "Applying" for step in proposal["steps"]):
            return ["A prior attempt ended while a step was Applying; manual recovery is required."]

        for step in pending:
            target_id = str(step["target_id"])
            if step["operation"] == "PATCH account" and target_id in current_accounts:
                live = self.crm.get_account(target_id).raw
                changed = self._differences(current_accounts[target_id], live, ACCOUNT_FIELDS)
                if changed:
                    conflicts.append(f"Account {target_id} changed: {', '.join(changed)}")
            elif step["operation"] == "PATCH contact" and target_id in contact_snapshots:
                live = self.crm.get_contact(target_id).raw
                changed = self._differences(contact_snapshots[target_id], live, CONTACT_FIELDS)
                if changed:
                    conflicts.append(f"Contact {target_id} changed: {', '.join(changed)}")

        if any(step["operation"] == "POST account" for step in pending):
            desired = proposal["desired"]
            excluded = set(current_accounts)
            for account in self.crm.fetch_accounts():
                if account.account_id in excluded:
                    continue
                same_place = (
                    normalize.address(account.billing_street) == normalize.address(str(desired.get("billing_street", "")))
                    and normalize.text(account.billing_city) == normalize.text(str(desired.get("billing_city", "")))
                    and normalize.text(account.billing_state) == normalize.text(str(desired.get("billing_state", "")))
                    and normalize.zip_code(account.billing_zip) == normalize.zip_code(str(desired.get("billing_zip", "")))
                )
                if same_place:
                    conflicts.append(
                        f"Account {account.account_id} now occupies the proposed facility address."
                    )
        return conflicts

    @staticmethod
    def _resolve_request(request: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        by_sequence = {step["sequence"]: step for step in steps}
        for key, value in request.items():
            if isinstance(value, str) and value.startswith("$step_") and value.endswith(".account_id"):
                sequence_text = value[len("$step_") : -len(".account_id")]
                dependency = by_sequence.get(int(sequence_text))
                if not dependency or dependency["status"] != "Applied" or not dependency["target_id"]:
                    raise ValueError(f"Unresolved step dependency: {value}")
                resolved[key] = dependency["target_id"]
            else:
                resolved[key] = value
        return resolved

    def execute(self, proposal_id: int) -> str:
        proposal = self.store.get_proposal(proposal_id)
        if not proposal:
            raise KeyError(proposal_id)
        if proposal["status"] not in {"Approved", "Partially Applied", "Failed"}:
            raise ValueError("Proposal must be Approved before execution")
        if not proposal["writable"]:
            raise ValueError("Proposal is not writable")

        try:
            conflicts = self._preflight(proposal)
        except Exception as exc:
            self.store.set_proposal_status(
                proposal_id, "Failed", "preflight_failed",
                {"error_type": type(exc).__name__, "message": str(exc)[:1000]},
            )
            return "Failed"
        if conflicts:
            self.store.set_proposal_status(
                proposal_id, "Conflict", "execution_conflict", {"conflicts": conflicts}
            )
            return "Conflict"

        self.store.set_proposal_status(
            proposal_id, "Applying", "execution_started", {"pending_steps": [
                step["sequence"] for step in proposal["steps"]
                if step["status"] in {"Pending", "Failed"}
            ]}
        )
        applied_before = any(step["status"] == "Applied" for step in proposal["steps"])
        applied_now = False
        for step in proposal["steps"]:
            if step["status"] == "Applied":
                continue
            if step["status"] not in {"Pending", "Failed"}:
                continue
            self.store.start_step(int(step["step_id"]))
            try:
                request = self._resolve_request(step["request"], proposal["steps"])
                if step["operation"] == "POST account":
                    response_status, response = self.crm.create_account(request)
                    created_id = str(response.get("account_id", ""))
                    if not created_id:
                        raise IndeterminateWriteError(
                            "Account creation returned success without account_id"
                        )
                    step["target_id"] = created_id
                    step["status"] = "Applied"
                    self.store.finish_step(
                        int(step["step_id"]), "Applied", response_status, response,
                        target_id=created_id,
                    )
                elif step["operation"] == "PATCH account":
                    response_status, response = self.crm.update_account(
                        str(step["target_id"]), request
                    )
                    step["status"] = "Applied"
                    self.store.finish_step(
                        int(step["step_id"]), "Applied", response_status, response
                    )
                elif step["operation"] == "PATCH contact":
                    response_status, response = self.crm.update_contact(
                        str(step["target_id"]), request
                    )
                    step["status"] = "Applied"
                    self.store.finish_step(
                        int(step["step_id"]), "Applied", response_status, response
                    )
                else:
                    raise ValueError(f"Unsupported operation: {step['operation']}")
                applied_now = True
            except IndeterminateWriteError as exc:
                # start_step intentionally left this step as Applying. Retrying
                # could repeat a write that actually committed remotely.
                self.store.set_proposal_status(
                    proposal_id, "Conflict", "indeterminate_write",
                    {
                        "sequence": step["sequence"], "operation": step["operation"],
                        "error_type": type(exc).__name__, "message": str(exc)[:1000],
                    },
                )
                return "Conflict"
            except Exception as exc:
                self.store.finish_step(
                    int(step["step_id"]), "Failed", None,
                    {"error_type": type(exc).__name__, "message": str(exc)[:1000]},
                )
                status = "Partially Applied" if applied_before or applied_now else "Failed"
                self.store.set_proposal_status(
                    proposal_id, status, "execution_failed",
                    {"failed_sequence": step["sequence"], "error_type": type(exc).__name__},
                )
                return status

        self.store.set_proposal_status(
            proposal_id, "Applied", "execution_completed", {"result": "all steps applied"}
        )
        return "Applied"
