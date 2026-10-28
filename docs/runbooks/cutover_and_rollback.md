# Runbook: cutover from the PC to the VM, and rollback

Moves the production schedule from the PC to the Oracle VM. **At no time may both machines run
the prod DAG.** Two things make that hard to get wrong:

1. **The procedure:** the PC's DAG is paused before anything moves, and is never unpaused again
   unless you roll back.
2. **The guard:** `BANK_HEALTH.OPS.PROD_SCHEDULER` names the one machine allowed to run prod.
   `check_environment`, the first task of every prod run, stops the run (and alerts) on any other
   machine, and `record_run` writes nothing there. Even if both DAGs were unpaused by mistake, only
   one could get past its first task. Cutover is a single change of that row.
   - **The table is owned by `ACCOUNTADMIN`.** `BANK_PIPELINE_ROLE`, which both machines use, may
     only read it, so the pipeline can't rewrite the rule that restrains it. Only you change it.
   - **It fails safe.** If a run can't read the table (missing table, missing grant, Snowflake
     down), `check_environment` stops the run and the alert says why.

**Do it on a quiet day: not a Monday, and not while a run is going.** It takes about an hour,
most of it waiting for the manual run. Every step that changes something is marked **(stop)**:
do it only when the step before it checked out.

## What is carried over, and how we check it

| State | Where it lives | How it moves |
|---|---|---|
| Watermarks `fdic_last_repdte`, `cfpb_last_date_received` | Airflow metadata database (Variables) | `pg_dump` / `pg_restore` of the whole database |
| Run history (DAG runs, task tries) | the metadata database | the same |
| Cosmos dbt cache | the metadata database (a Variable) | the same; it would rebuild itself anyway |
| The Airflow admin user | the metadata database | the same |
| Connections (Snowflake, Databricks) | **not** in the database: built from `.env` when containers start | the `.env` you copied in [oracle_vm_setup.md](oracle_vm_setup.md) |
| Fernet key (decrypts stored values) | `.env` | the same `.env`, so the restored values decrypt |
| Downloaded files (`data/raw`) | the PC's disk | not needed: the VM re-downloads only what the watermarks say is new, and uploads only files the Databricks Volume lacks |

Why the whole database rather than just exporting the two Variables: it also keeps the run
history, and it's tiny (about 13 MB). Both machines run Postgres 16 and Airflow 3.3.2, so the
restore needs no migration. The Variable export is the cross-check.

**The check:** run `../deploy/airflow_state.sh` from the `airflow/` folder on both machines
(Git Bash on the PC). Both must show the same watermarks, the same number of
`bank_health_weekly` runs, the same last successful run id, 0 runs in progress, and 0 stored
connections. Today the PC shows:

```
fdic_last_repdte           2026-06-30
cfpb_last_date_received    2026-10-25
bank_health_weekly         paused=False runs=2   last success=scheduled__2026-10-26T11:00:00+00:00
runs in progress: 0
connections stored in the database: 0
```

## Before the day

- The VM is set up ([oracle_vm_setup.md](oracle_vm_setup.md)), its deploys work
  ([deploy.md](deploy.md)), and it runs the same `main` as the PC.
- `snowflake/setup_ops.sql` has been run: `BANK_HEALTH.OPS.PROD_SCHEDULER` says `pc`.
- The PC's prod `.env` has `PROD_HOST_ID=pc`; the VM's has `PROD_HOST_ID=oracle-vm` and both
  prod webhooks ([alerts.md](alerts.md)).
- On the VM, **both DAGs are paused** (`bank_health_weekly`, `bank_health_slo_check`). They
  start paused on a fresh database.

## Cutover

PC commands run in PowerShell in `C:\Users\marti\projects\bank-health-data-platform\airflow`.
VM commands run as `deploy` in `/srv/bank-health/app/airflow` (`ssh bank-health-vm`).

1. **(stop) Pause prod on the PC.**
   ```powershell
   docker compose exec airflow-scheduler airflow dags pause bank_health_weekly
   docker compose exec airflow-scheduler airflow dags pause bank_health_slo_check
   ```
2. **Check that nothing is running on the PC:** in Git Bash, `../deploy/airflow_state.sh` shows
   `runs in progress: 0` and `paused=True` for both. If a run is going, wait for it to end.
3. **Dump the PC's metadata database:**
   ```powershell
   docker compose exec postgres pg_dump -U airflow -d airflow -Fc -f /tmp/airflow.dump
   docker compose cp postgres:/tmp/airflow.dump .\airflow.dump
   ```
   The dump holds encrypted values and the admin password hash: treat it like a secret, and
   delete it after step 6.
4. **Copy it to the VM:**
   ```powershell
   scp .\airflow.dump bank-health-vm:/srv/bank-health/var/airflow.dump
   ```
5. **(stop) Restore it on the VM.** Keep the VM's Postgres running, stop the Airflow services,
   restore, then start again:
   ```bash
   docker compose stop airflow-apiserver airflow-scheduler airflow-dag-processor
   docker compose cp /srv/bank-health/var/airflow.dump postgres:/tmp/airflow.dump
   docker compose exec postgres pg_restore -U airflow -d airflow --clean --if-exists --no-owner /tmp/airflow.dump
   docker compose up airflow-init          # migrate check: nothing to do, same Airflow version
   docker compose up -d --wait
   ../deploy/smoke_check.sh
   ```
6. **Compare:** `../deploy/airflow_state.sh` on the VM must match step 2 on the PC (watermarks,
   run count, last success, both DAGs **paused**). Then delete the dump files on both machines
   (`Remove-Item .\airflow.dump` on the PC; `rm /srv/bank-health/var/airflow.dump` on the VM).
7. **(stop) Hand the schedule to the VM.** In Snowsight, as ACCOUNTADMIN:
   ```sql
   UPDATE BANK_HEALTH.OPS.PROD_SCHEDULER
      SET HOST_ID = 'oracle-vm', SET_AT = CURRENT_TIMESTAMP(), SET_BY = CURRENT_USER(), NOTE = 'cutover';
   SELECT * FROM BANK_HEALTH.OPS.PROD_SCHEDULER;   -- one row: oracle-vm
   ```
   From now on the PC can't run prod even if unpaused: its `check_environment` would stop.
8. **(stop) Unpause on the VM:**
   ```bash
   docker compose exec airflow-scheduler airflow dags unpause bank_health_weekly
   docker compose exec airflow-scheduler airflow dags unpause bank_health_slo_check
   ```
9. **(stop) Trigger one manual run on the VM:**
   ```bash
   docker compose exec airflow-scheduler airflow dags trigger bank_health_weekly
   ```
10. **Check the result:**
    - The run succeeds. Watch it in the UI through the tunnel.
    - The `check_environment` log says `this machine (oracle-vm) is the one assigned`.
    - An *OK* message arrives in **both prod channels**.
    - A new row with `HOST_ID = 'oracle-vm'` is in
      `SELECT * FROM BANK_HEALTH.OPS.HEALTH_RUNS_LAST_12 ORDER BY STARTED_AT DESC`.
    - Row counts match the last PC run (Monday's): rows per layer and scored banks.
    - From the PC: `pytest tests/e2e -m e2e` with `AIRFLOW_URL=http://localhost:8090` while the
      tunnel is open.
11. **(stop) Stop the PC stack. Don't delete it.**
    ```powershell
    docker compose stop
    ```
    `stop` keeps the containers, the image and the metadata database. With
    `restart: unless-stopped` the stack stays stopped after a reboot. Keep it as the rollback for
    **3 weeks**. Write down the date: deleting it after that is a separate decision, and you'll
    be asked first.
12. In `~/.ssh/config`, you may now change `LocalForward 8090` to `8080`, so the log links in
    alerts (`http://localhost:8080/...`) open as they are.

**Acceptance (O-01):** three Monday runs in a row succeed on the VM **with the PC switched off**:
three successful `HOST_ID = 'oracle-vm'` rows, on three consecutive Mondays, in
`OPS.PIPELINE_RUNS`.

## Rollback to the PC (within the 3 weeks)

1. **(stop)** On the VM, pause both DAGs (step 1's commands). Check that no run is in progress.
2. Dump the VM's database and copy it to the PC: steps 3–4 in reverse (`docker compose exec
   postgres pg_dump …` on the VM, `scp bank-health-vm:/srv/bank-health/var/airflow.dump .` on the
   PC).
3. **(stop)** On the PC: `docker compose start postgres`, restore as in step 5 (`docker compose
   cp`, `pg_restore --clean --if-exists --no-owner`), then `docker compose start`.
4. **(stop)** In Snowsight, as ACCOUNTADMIN:
   `UPDATE BANK_HEALTH.OPS.PROD_SCHEDULER SET HOST_ID = 'pc', SET_AT = CURRENT_TIMESTAMP(), SET_BY = CURRENT_USER(), NOTE = 'rollback';`
5. **(stop)** Unpause both DAGs on the PC, and trigger one run to check it.

## The PostgreSQL mirror after the move

The PostgreSQL mirror and pgAdmin stay on the PC. They only **read** Snowflake and Databricks, so
they don't need the prod stack. After each Monday run, refresh the mirror on the PC as today:

```powershell
cd C:\Users\marti\projects\bank-health-data-platform
docker compose -f postgres\docker-compose.yaml up -d
.venv\Scripts\python scripts/mirror_to_postgres.py
```

It uses the prod `.env` on the PC, read-only; the one-scheduler guard doesn't affect it.
