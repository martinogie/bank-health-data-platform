# Runbook: the Databricks jobs as code (Asset Bundle)

Phase 4, PR 9. `databricks.yml` defines the two Databricks jobs; the bundle **owns** them:

| Target | Job | Deployed by |
|---|---|---|
| dev | `bank_health_pipeline_dev` | Martins' PC: `databricks bundle deploy -t dev --profile bank-health` |
| prod | `bank_health_pipeline` | GitHub workflow "Databricks bundle (prod)", after the `production` approval |

Both existing jobs were **bound** to the bundle (6 Nov 2026), so deploys update them; nothing is
recreated and no duplicate appears. Bundle-managed jobs are locked in the Databricks UI
(`UI_LOCKED`): change them in `databricks.yml`, not by hand.

The notebooks still reach the workspace as before (prod: the Git folder pulled to main; dev:
uploaded from a checkout), so the bundle uploads no files. Airflow's `sync_databricks_job` makes the
notebooks current and **checks** the job exists; it no longer rewrites the job.

## Change the job definition

1. Edit `databricks.yml` (and `ingestion/databricks_jobs.py`'s `NOTEBOOK_TASKS` /
   `ENVIRONMENT_VERSION`: a unit test keeps the two the same).
2. Dev: `databricks bundle plan -t dev --profile bank-health`, then `databricks bundle deploy -t dev
   --profile bank-health`.
3. Prod: merge to main. The workflow runs `validate`, `plan` (it **refuses** any plan that would add
   or delete a job: only updates may go through), then waits for your approval and deploys.

## One-time setup for the prod workflow (Martins)

1. **Databricks token** (Settings → Developer → Access tokens → Generate new token): comment
   `bank-health bundle deploy (GitHub)`, lifetime **90 days**. Copy it once.
2. **GitHub → Settings → Environments → production → Environment secrets → Add secret**:
   - `DATABRICKS_TOKEN_BUNDLE`: the token;
   - `DATABRICKS_HOST`: the workspace address (`https://dbc-…cloud.databricks.com`; kept out of
     the public repo).
3. The weekly `check_credentials` task lists your Databricks tokens and sends an **EXPIRING**
   message 14 days before this one runs out. Then repeat steps 1–2 (replace the secret) and delete
   the old token.

## Done on 6 Nov 2026

- `databricks bundle validate`: dev and prod OK (warning "no files to sync": expected).
- Dev: bound; plan **0 to add, 1 to change, 0 to delete**; **one dev deploy** (0 created, 1 changed,
  0 deleted; still 2 jobs). The dev job's settings before and after differ only in the bundle
  marker (`deployment`, `edit_mode`) and the order Databricks lists the tasks in; 0 field
  differences in any task.
- Prod: bound (the prod job unchanged by it); plan **0 to add, 1 to change, 0 to delete**. The prod
  deploy itself goes through the GitHub approval.

## If something goes wrong

| You see | Do this |
|---|---|
| `sync_databricks_job` fails: `no job named ... deploy the bundle` | the job was deleted or renamed; deploy the bundle for that target |
| The workflow says `REFUSED: the plan would add or delete a job` | the binding is lost (e.g. the bundle state folder was deleted): bind again, `databricks bundle deployment bind pipeline <job id> -t prod`, then re-run |
| A job must be changed urgently by hand | `databricks bundle deployment unbind pipeline -t <target>` unlocks it; bind again afterwards |
