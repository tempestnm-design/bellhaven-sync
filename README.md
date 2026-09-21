# Ownership Reconciliation

Clipboard Health Sales Operations Analyst take-home exercise.

This repository implements a configurable ownership-reconciliation pipeline.
Bellhaven Senior Living is the assessment profile, not a hardcoded matching
rule. The system discovers current facilities, retrieves the CRM population,
creates evidence-backed proposals, and requires human approval before writes.

## Current status

Batch 2 implements read-only website/CRM ingestion, normalization, deterministic
matching, business-rule classification, evidence-backed proposal generation,
stable fingerprints, and SQLite audit records. The review and execution module
follows in Batch 3. There is currently no CRM write path.

## Quick start

```bash
cp .env.example .env
# Add the assessment token to your local .env or shell environment.
export CLIPBOARD_CRM_TOKEN='...'
# Full read-only ingestion and reconciliation:
python run_pipeline.py --operator bellhaven

# Optional ingestion-only diagnostic:
python run_pipeline.py --operator bellhaven --ingest-only
python -m unittest discover -s tests -v
```

The full run prints source counts, match classifications, and the number of
new proposals queued. Identical proposal fingerprints are stored once, so a
second run against unchanged evidence does not create duplicate queue items.

Runtime databases, snapshots, logs, and secrets are ignored by Git.
