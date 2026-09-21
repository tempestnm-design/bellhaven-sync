# Ownership Reconciliation

Clipboard Health Sales Operations Analyst take-home exercise.

This repository implements a configurable ownership-reconciliation pipeline.
Bellhaven Senior Living is the assessment profile, not a hardcoded matching
rule. The system discovers current facilities, retrieves the CRM population,
creates evidence-backed proposals, and requires human approval before writes.

## Current status

Batch 1 implements read-only website and CRM ingestion plus SQLite run
snapshots. Reconciliation and the review application follow in later batches.

## Quick start

```bash
cp .env.example .env
# Add the assessment token to your local .env or shell environment.
export CLIPBOARD_CRM_TOKEN='...'
python run_pipeline.py --operator bellhaven --ingest-only
python -m unittest discover -s tests -v
```

Runtime databases, snapshots, logs, and secrets are ignored by Git.
