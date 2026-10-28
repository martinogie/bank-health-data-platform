# ADR 0003: Living with Databricks Free Edition's limits

- **Status:** accepted (Phase 2, October 2026)
- **Findings:** O-04 (Databricks Free Edition limits are a hidden risk)

## Context

Bronze, silver, gold, training and scoring run on Databricks Free Edition. Its limits:

- **Serverless compute only**: no custom clusters.
- **A daily compute quota**: when it's used up, compute stops for the rest of the day.
- **At most 5 concurrent job tasks.**
- **Restricted outbound internet**: notebooks can't download the FDIC and CFPB data themselves.
- **One small SQL warehouse.**

Earlier runs had to be resumed after failures in `01_bronze` and `05_score`.

## Decision

Keep Databricks Free Edition, and design around its limits:

1. **Downloads happen in Airflow**, which uploads files into a Unity Catalog Volume; notebooks
   never need the internet.
2. **The job is one chain of five notebook tasks** (`bronze → silver → gold → train_model → score`),
   well under the 5-task limit, with one run at a time and **one** Airflow retry, to protect the
   quota.
3. **Each notebook can be re-run on its own:**
   - bronze uses Auto Loader checkpoints;
   - silver, gold and scores overwrite their tables;
   - training is skipped unless a new quarter arrived or a retrain was asked for.
   After a quota stop, **Repair run** in Databricks resumes from the failed notebook the next day
   ([quota_errors.md](../runbooks/quota_errors.md)).
4. **A quota stop is named in the alert** ("Databricks Free Edition daily quota reached"), not
   reported as a generic failure.
5. **Dev shares the quota**, so dev runs use a small slice and never run near the Monday prod run.

## Alternatives considered

| Option | Why not now |
|---|---|
| Databricks paid (pay-as-you-go) workspace | Removes the quota, but costs money every run; revisit if runs regularly hit the limit |
| Run the Spark steps locally or on the VM | Loses Unity Catalog, MLflow model registry and the Databricks experience the project demonstrates |
| Split the job across days | More moving parts for a weekly job that normally fits in one day |

## Consequences

- A quota stop delays the week's data by a day. The late-run alert and the runbook make it
  visible and recoverable.
- The run log (`OPS.PIPELINE_RUNS`) records each run's duration: a steady climb is the early
  warning that the job is outgrowing the quota.
