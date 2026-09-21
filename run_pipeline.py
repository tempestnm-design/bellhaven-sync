#!/usr/bin/env python3
"""Run a read-only ingestion snapshot (Batch 1)."""

from __future__ import annotations

import argparse
import sys

from reconciliation.config import database_path, get_required_token, load_profile
from reconciliation.crm import CrmClient
from reconciliation.db import SnapshotStore
from reconciliation.errors import ReconciliationError
from reconciliation.sources import BellhavenSource


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator", default="bellhaven")
    parser.add_argument("--ingest-only", action="store_true", help="Accepted for forward compatibility")
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
        print(
            f"Run {run_id} succeeded: {len(facilities)} website facilities, "
            f"{len(accounts)} CRM accounts, parent {parent.account_id}."
        )
        return 0
    except (ReconciliationError, OSError) as exc:
        print(f"Ingestion failed closed: {exc}", file=sys.stderr)
        return 1
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
