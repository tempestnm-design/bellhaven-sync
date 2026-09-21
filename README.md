# Ownership Reconciliation

Clipboard Health Sales Operations Analyst take-home exercise.

This repository implements a configurable ownership-reconciliation pipeline.
Bellhaven Senior Living is the assessment profile, not a hardcoded matching
rule. The system discovers current facilities, retrieves the CRM population,
creates evidence-backed proposals, and requires human approval before writes.

## System boundary

The scheduled pipeline is read-only. It ingests source data, reconciles records,
and stores recommendations as `Pending`. CRM writes exist only in the local
review application and require two separate human actions: record an approval,
then explicitly execute that approved proposal. Execution re-fetches affected
records and stops on live-state drift.

## Quick start

```bash
python -m pip install -r requirements.txt
export CLIPBOARD_CRM_TOKEN='...'
# Full read-only ingestion and reconciliation:
python run_pipeline.py --operator bellhaven

# Optional ingestion-only diagnostic:
python run_pipeline.py --operator bellhaven --ingest-only
python -m unittest discover -s tests -v

# Local review application (binds only to 127.0.0.1):
export APP_SECRET_KEY='replace-with-a-random-local-value'
export REVIEWER_NAME='Your Name'
python app.py
```

The full run prints source counts, match classifications, and the number of
new proposals queued. Identical proposal fingerprints are stored once, so a
second run against unchanged evidence does not create duplicate queue items.

Runtime databases, snapshots, logs, and secrets are ignored by Git.

See [Operating guide](docs/OPERATIONS.md) for the review, execution, conflict,
partial-recovery, and audit workflows.
