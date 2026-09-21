#!/usr/bin/env python3
"""Local-only human review and approved execution application."""

from __future__ import annotations

import hmac
import os
import secrets
from collections.abc import Callable
from pathlib import Path

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for,
)

from reconciliation.config import database_path, get_required_token, load_profile
from reconciliation.crm import CrmClient
from reconciliation.db import SnapshotStore
from reconciliation.executor import ProposalExecutor


CrmFactory = Callable[[], CrmClient]


def create_app(
    test_config: dict[str, object] | None = None,
    crm_factory: CrmFactory | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("APP_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=str(database_path()),
        REVIEWER_NAME=os.environ.get("REVIEWER_NAME", "Local Reviewer"),
    )
    if test_config:
        app.config.update(test_config)

    def get_store() -> SnapshotStore:
        if "store" not in g:
            g.store = SnapshotStore(Path(str(app.config["DATABASE"])))
        return g.store

    def get_crm() -> CrmClient:
        if crm_factory:
            return crm_factory()
        profile = load_profile("bellhaven")
        return CrmClient(profile, get_required_token(profile))

    @app.teardown_appcontext
    def close_store(_error: BaseException | None) -> None:
        store = g.pop("store", None)
        if store:
            store.close()

    @app.before_request
    def csrf_token() -> None:
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        if request.method == "POST":
            supplied = request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not supplied or not hmac.compare_digest(supplied, expected):
                abort(400, "Invalid CSRF token")

    @app.context_processor
    def template_context() -> dict[str, object]:
        return {
            "csrf_token": session.get("csrf_token", ""),
            "reviewer_name": app.config["REVIEWER_NAME"],
        }

    @app.get("/")
    def index() -> str:
        filters = {
            key: request.args.get(key, "")
            for key in ("status", "classification", "confidence", "run_id", "facility")
        }
        proposals = get_store().list_proposals(filters)
        all_proposals = get_store().list_proposals()
        options = {
            "statuses": sorted({str(item["status"]) for item in all_proposals}),
            "classifications": sorted({str(item["classification"]) for item in all_proposals}),
            "confidences": sorted({str(item["confidence"]) for item in all_proposals}),
            "runs": sorted({int(item["run_id"]) for item in all_proposals}, reverse=True),
        }
        counts = {
            status: sum(item["status"] == status for item in all_proposals)
            for status in ("Pending", "Approved", "Rejected", "Applied", "Conflict", "Failed")
        }
        return render_template(
            "index.html", proposals=proposals, filters=filters, options=options, counts=counts
        )

    @app.get("/proposals/<int:proposal_id>")
    def proposal_detail(proposal_id: int) -> str:
        proposal = get_store().get_proposal(proposal_id)
        if not proposal:
            abort(404)
        return render_template("proposal.html", proposal=proposal)

    @app.post("/proposals/<int:proposal_id>/approve")
    def approve(proposal_id: int):
        try:
            get_store().decide_proposal(
                proposal_id, "Approved", request.form.get("reviewer_name", ""),
                request.form.get("reason", ""),
            )
            flash("Proposal approved. No CRM write has occurred yet.", "success")
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("proposal_detail", proposal_id=proposal_id))

    @app.post("/proposals/<int:proposal_id>/reject")
    def reject(proposal_id: int):
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("A rejection reason is required for the audit trail.", "error")
            return redirect(url_for("proposal_detail", proposal_id=proposal_id))
        try:
            get_store().decide_proposal(
                proposal_id, "Rejected", request.form.get("reviewer_name", ""), reason
            )
            flash("Proposal rejected and its fingerprint will remain suppressed.", "success")
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("proposal_detail", proposal_id=proposal_id))

    @app.post("/proposals/<int:proposal_id>/execute")
    def execute(proposal_id: int):
        if request.form.get("confirmation") != "execute-approved-proposal":
            flash("Execution confirmation was missing.", "error")
            return redirect(url_for("proposal_detail", proposal_id=proposal_id))
        try:
            status = ProposalExecutor(get_store(), get_crm()).execute(proposal_id)
            category = "success" if status == "Applied" else "warning"
            flash(f"Execution finished with status: {status}.", category)
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        return redirect(url_for("proposal_detail", proposal_id=proposal_id))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
