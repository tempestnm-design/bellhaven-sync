#!/usr/bin/env python3
"""Run a read-only ingestion snapshot (Batch 1)."""

from __future__ import annotations

import argparse
import sys

from reconciliation.config import database_path, get_required_token, load_profile
from reconciliation.crm import CrmClient
from reconciliation.db import SnapshotStore
from reconciliation.errors import ReconciliationError
from reconciliation.matcher import match_facilities
from reconciliation.proposals import build_proposals
from reconciliation.sources import BellhavenSource


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator", default="bellhaven")
    parser.add_argument("--ingest-only", action="store_true", help="Stop after validated snapshots")
    args = parser.parse_args()

    store: SnapshotStore | None = None
    try:
        profile = load_profile(args.operator)
        token = get_required_token(profile)
        store = SnapshotStore(database_path())
        with store.run(profile.operator_key) as run_id:
            facilities = BellhavenSource(profile).fetch_facilities()
            crm = CrmClient(profile, token)
            crm.verify_workspace()
            accounts = crm.fetch_accounts()
            parent = crm.validate_parent(accounts)
            store.save_facilities(run_id, facilities)
            store.save_accounts(run_id, accounts)
            if args.ingest_only:
                results = []
                proposals = []
                inserted = 0
            else:
                results = match_facilities(facilities, accounts, parent.account_id)
                duplicate_ids = {
                    account.account_id
                    for result in results if result.classification == "duplicate_group"
                    for account in result.selected_accounts
                }
                contacts = [
                    contact
                    for account_id in sorted(duplicate_ids)
                    for contact in crm.fetch_contacts(account_id)
                ]
                proposals = build_proposals(
                    profile.operator_key, parent.account_id, profile.care_type_map, results, contacts
                )
                store.save_matches(run_id, results)
                inserted = store.save_proposals(run_id, proposals)
        summary = (
            f"Run {run_id} succeeded: {len(facilities)} website facilities, "
            f"{len(accounts)} CRM accounts, parent {parent.account_id}"
        )
        if args.ingest_only:
            print(summary + ".")
        else:
            classifications: dict[str, int] = {}
            for result in results:
                classifications[result.classification] = classifications.get(result.classification, 0) + 1
            print(
                summary + f"; {len(results)} match results, {len(proposals)} derived proposals, "
                f"{inserted} newly queued."
            )
            print("Classifications: " + ", ".join(f"{key}={value}" for key, value in sorted(classifications.items())))
        return 0
    except (ReconciliationError, OSError) as exc:
        print(f"Ingestion failed closed: {exc}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
