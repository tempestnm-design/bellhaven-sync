# Operating guide

## Safety model

`run_pipeline.py` never writes to the CRM. It may run manually or on a daily
schedule and can only create local `Pending` recommendations.

The local review application is the sole path to CRM mutation:

1. A reviewer inspects website evidence, current CRM state, financial exposure,
   matching signals, desired state, and every ordered operation.
2. Approve records the reviewer, note, timestamp, and immutable fingerprint. It
   does not execute anything.
3. Execute requires a second explicit confirmation.
4. Immediately before writing, the executor re-fetches every pending target and
   compares relevant business fields with the proposal snapshot.
5. Drift marks the proposal `Conflict`; no operation is issued.
6. Each API attempt and response is stored without authorization headers.

Rejecting requires a reason. The unique fingerprint remains in the decision
ledger, preventing the unchanged recommendation from returning as pending.

## Local setup

```bash
python -m pip install -r requirements.txt
export CLIPBOARD_CRM_TOKEN='...'
export APP_SECRET_KEY='a-long-random-local-secret'
export REVIEWER_NAME='Neil McGuffin'
python run_pipeline.py --operator bellhaven
python app.py
```

Open `http://127.0.0.1:5000`. The application deliberately binds to localhost.
Do not commit `.env` or the SQLite database.

## Proposal states

| State | Meaning |
|---|---|
| `Pending` | Awaiting a human decision; cannot execute. |
| `Approved` | Decision recorded; no write has necessarily occurred. |
| `Rejected` | Decision recorded; identical fingerprint remains suppressed. |
| `Applying` | One approved execution is in progress. |
| `Applied` | Every ordered operation completed. |
| `Partially Applied` | Earlier steps succeeded and a later step failed. |
| `Conflict` | Live state drifted or an attempt ended indeterminately; rerun reconciliation. |
| `Failed` | The first pending step failed; an explicit retry is permitted. |

## Compound recovery

Successful steps are never repeated. A CHOW account creation stores the new
account ID on its step before the old account is linked. If linking fails, the
next explicit execution resumes at the link step and does not create another
account. Contact transfers and duplicate-account updates behave the same way.

If the process ends while a step is `Applying`, automatic retry is blocked as a
conflict because the remote result is indeterminate. Inspect the CRM and create
a fresh reconciliation proposal rather than risking a duplicate write.

## Conflict recovery

1. Inspect the audit event and changed fields.
2. Run `python run_pipeline.py --operator bellhaven` again.
3. Review the new evidence and fingerprint.
4. Approve and execute only the newly generated recommendation.

Do not manually change a `Conflict` proposal back to `Approved`.

## Audit evidence

The SQLite ledger retains:

- immutable website and CRM snapshots by run;
- match candidates and explanations;
- proposal fingerprint, evidence, current state, desired state, and ordered steps;
- reviewer identity, decision, reason, and timestamp;
- step attempts, request bodies, response status/body, returned created IDs, and timestamps;
- proposal-level approval, rejection, conflict, failure, and completion events.

Bearer tokens and Authorization headers are never persisted.

## Daily workflow

`.github/workflows/daily-reconciliation.yml` runs tests followed by the
read-only pipeline. It cannot call the executor or approve proposals. Its SQLite
output is uploaded as an audit artifact for manual retrieval.
