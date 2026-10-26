# Contributing

How changes get from an idea to the weekly production run without breaking it. The short
version: work on a branch in a **dev checkout**, test against **dev**, open a pull request, let
**CI** pass, review, merge. Production only ever runs `main`.

## Environments

`BANK_HEALTH_ENV` decides what a process may touch. It is required everywhere: if it is
missing or not one of the three values, everything stops with a clear error.

| | dev | ci | prod |
|---|---|---|---|
| Where | a dev checkout on your PC (its own folder, `.env`, `keys/`) | GitHub Actions | the machine that runs the schedule: the PC today, the Oracle Cloud Arm VM later |
| Snowflake | `BANK_HEALTH_DEV` as `SVC_BANK_DEV` / `DEV_ROLE`, warehouse `BANK_DEV_WH` | a temporary `BANK_HEALTH_DEV.CI_<run_id>` schema as `SVC_BANK_CI` / `CI_ROLE`; reads `BANK_HEALTH` | `BANK_HEALTH` as `SVC_BANK_PIPELINE` / `BANK_PIPELINE_ROLE`, warehouse `BANK_WH` |
| Databricks | catalog `bank_health_dev`, job `bank_health_pipeline_dev`, notebooks uploaded from the checkout | none | catalog `bank_health`, job `bank_health_pipeline`, notebooks from the Git folder on `main` |
| Airflow | `docker-compose.yaml` + `docker-compose.dev.yaml`, UI on port 8081 | DAG import only | `docker-compose.yaml`, UI on port 8080 |
| Model | registers `@candidate` in `bank_health_dev`; reads prod's `@champion`, never moves it | none | `@candidate`, and `@champion` after review |
| Publishes the watchlist and the dbt manifest | no | no | yes |

Two layers stop dev and CI from writing prod: the code (`ingestion/environment.py` guards
every write, publish and promotion) and Snowflake itself (`DEV_ROLE` and `CI_ROLE` have no
write grants on `BANK_HEALTH`; `snowflake/setup_dev.sql`). Both are tested.

### Set up a dev checkout (once)

```bash
git worktree add ../bank-health-data-platform-dev -b feature/my-change origin/main
cd ../bank-health-data-platform-dev
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt -r requirements-lint.txt
.venv/Scripts/pip install pre-commit && .venv/Scripts/pre-commit install   # hooks on every commit
```

Then, in that folder:
- `.env`: copy `.env.example`, set `BANK_HEALTH_ENV=dev` and the dev Snowflake user, role,
  warehouse and database (`SVC_BANK_DEV`, `DEV_ROLE`, `BANK_DEV_WH`, `BANK_HEALTH_DEV`).
- `keys/`: the dev private key only.
- `airflow/.env` (git-ignored), so a plain `docker compose` here always means the dev stack:
  ```
  COMPOSE_PROJECT_NAME=bank-health-dev
  COMPOSE_FILE=docker-compose.yaml;docker-compose.dev.yaml
  ```
  (use `:` instead of `;` on Linux and macOS)

Never switch the production folder to a feature branch: its Airflow stack runs whatever is
checked out there.

## Branches

From an up-to-date `main`, one branch per change:

| Prefix | For |
|---|---|
| `feature/` | new behaviour (`feature/phase1-ci-environments`) |
| `fix/` | a bug or a wrong number (`fix/model-credibility`) |
| `docs/` | documentation only (`docs/v7-plan`) |
| `chore/` | dependencies, tooling, CI (`chore/bump-dbt`) |

`main` is protected: changes arrive only through a pull request with every CI check green.
No force pushes. Merge commits are fine.

## How changes are tested

| Step | What runs | Where |
|---|---|---|
| On commit | ruff (lint + format), end of file, trailing whitespace, private keys, gitleaks | pre-commit, on your PC |
| On your PC | `pytest` (unit, offline), and for the parts you touched: `pytest tests/integration -m integration`, a dev DAG run, `pytest tests/smoke -m smoke`, `cd dbt && dbt build` (target dev) | dev checkout |
| On push / pull request | `lint`, `types`, `unit` (coverage gates 80%), `dag-import`, `docker (linux/amd64)`, `docker (linux/arm64)`, `secrets`, `dbt-slim-ci` | GitHub Actions (`.github/workflows/ci.yml`) |
| After merge | the next scheduled prod run, its `check_environment` and `sanity_check_top20` tasks, then `pytest tests/e2e -m e2e` | prod |

**dbt slim CI** builds only the dbt models a pull request changed, plus everything downstream,
into a throwaway schema `BANK_HEALTH_DEV.CI_<run_id>`, and reads every unchanged model from prod.
It compares against the manifest of the latest successful prod dbt run, which prod Airflow
uploads to `@BANK_HEALTH.RAW.DBT_ARTIFACTS` (task `publish_dbt_manifest`). Until the first
prod run after this was added, the stage is empty and CI builds everything. Without the
Snowflake secrets (forks, Dependabot) the job skips and passes.

If a CI job was killed before its clean-up step, find and drop leftover schemas (as
ACCOUNTADMIN or `CI_ROLE`):

```sql
SHOW SCHEMAS LIKE 'CI\\_%' IN DATABASE BANK_HEALTH_DEV;
DROP SCHEMA IF EXISTS BANK_HEALTH_DEV.CI_<run_id>;
```

## Pull request checklist

- [ ] Branch from an up-to-date `main`, named as above.
- [ ] Tested in dev (not prod): unit tests, plus integration / smoke / dbt for what changed.
- [ ] Every CI check is green.
- [ ] No secrets or account identifiers in the diff (gitleaks checks secrets; also read the diff).
- [ ] README, `docs/` and the Testing table updated if behaviour or numbers changed. Only real
      results, with their date.
- [ ] Model changes: release review below done, result recorded in the PR.
- [ ] **Prod `.env` still has `BANK_HEALTH_ENV=prod`**, and no `DATABRICKS_CATALOG` line
      (the catalog follows the environment).
- [ ] If `airflow/Dockerfile`, `airflow/requirements.txt` or `airflow/include/` changed: after
      merging, rebuild and restart prod (`docker compose build && docker compose up -d` in
      `airflow/`).
- [ ] Nothing scheduled is running (Mondays 06:00 America/Chicago) when you merge and restart.

## Release review (model changes)

A new model version is registered as `@candidate` and only becomes `@champion` after a person
has reviewed it. The full steps are in [docs/runbook.md](docs/runbook.md); in short:

1. Score with the candidate into a separate table, in prod:
   `.venv\Scripts\python scripts/databricks_run.py 05_score --param=model_alias=candidate`
2. **Top-20 check:**
   `.venv\Scripts\python scripts/sanity_check_top20.py --databricks ml.risk_scores_candidate`
   fails on a new bank, a foreign-bank office, a 0% or missing Tier 1 ratio, or a reason quoting
   an unusable capital ratio. Then read the 20 names yourself and look up two or three on FDIC
   BankFind.
3. Compare with the champion on the same test rows (MLflow: ROC-AUC, PR-AUC, calibration, band
   counts).
4. Record date, version and outcome in the pull request; promote only after approval. Only prod
   can move `@champion`; dev refuses `promote=true`.
