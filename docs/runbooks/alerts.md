# Runbook: alerts

Every alert goes to **both Slack and Discord**: the same message, through an incoming webhook
for each. Dev and prod have **separate webhooks posting to separate channels**, so a dev test
never reaches the prod channel. Why both, and why webhooks:
[ADR 0002](../adr/0002-alert-channels-slack-and-discord.md).

| Alert | Sent when | Sent by |
|---|---|---|
| **FAILED: bank_health_weekly / \<task\>** | a task fails after its last retry | `on_failure_callback` on every task (`ingestion/alerts.py`) |
| … **Databricks Free Edition daily quota reached** | the failure text is a Databricks quota stop | the same, with that line added ([quota_errors.md](quota_errors.md)) |
| **FAILED … check_freshness** | marts not rebuilt this run, an old FDIC quarter, or scored banks out of range | task `check_freshness` |
| **FAILED … check_volumes** | a layer's row count dropped or jumped beyond its threshold | task `check_volumes` |
| **LATE: this week's bank data is not ready** | no successful run by Monday 12:00 CT | DAG `bank_health_slo_check` on the prod machine |
| **LATE (seen from GitHub)** / **WATCHDOG: could not check** | the same, checked from outside the VM, Monday 12:30 CST (13:30 CDT) | GitHub Actions `late_run_watchdog.yml` |
| **OK: bank_health_weekly succeeded** | every successful run: rows, duration, dbt results | task `record_run` |

**Silence is not success.** Expect one *OK* message every Monday morning. No message by lunchtime
means something is wrong, even if no alert arrived.

A message never contains a webhook URL or any other secret: `ingestion/alerts.py` masks them
everywhere. An alert that can't be sent never fails the pipeline; it logs a warning naming the
channel that failed.

## Setup (you do this; the URLs are secrets)

### 1. Four webhooks

| | Slack | Discord |
|---|---|---|
| Channels | `#bank-health-prod`, `#bank-health-dev` | `#bank-health-prod`, `#bank-health-dev` |
| How | <https://api.slack.com/apps> → **Create New App** → *From scratch* (name `Bank Health alerts`, your workspace) → **Incoming Webhooks** → on → **Add New Webhook to Workspace** → choose the channel → **Allow** → copy the URL. Repeat for the other channel. | Server settings → **Integrations** → **Webhooks** → **New Webhook** → name `Bank Health`, choose the channel → **Copy Webhook URL**. Repeat for the other channel. |

No bot tokens and no extra Airflow providers are needed: a webhook can only post to its one
channel, which is all an alert needs.

### 2. Paste them into the right `.env` (two lines each)

```
SLACK_WEBHOOK_URL=<the Slack URL for this environment's channel>
DISCORD_WEBHOOK_URL=<the Discord URL for this environment's channel>
```

| File | Which webhooks |
|---|---|
| Dev checkout `.env` (e.g. `C:\Users\marti\projects\bank-health-data-platform-phase1\.env`) | the **dev** pair |
| Prod `.env` on the PC (`C:\Users\marti\projects\bank-health-data-platform\.env`) | the **prod** pair |
| Prod `.env` on the VM (`/srv/bank-health/secrets/.env`, edit with `nano`) | the **prod** pair |

Each prod `.env` also gets its machine name: `PROD_HOST_ID=pc` on the PC, `PROD_HOST_ID=oracle-vm`
on the VM.

Containers read `.env` when they're created, so recreate them after editing it
(`docker compose up -d --force-recreate` in `airflow/`).

### 3. GitHub secrets for the outside watchdog (the prod pair)

```powershell
gh secret set SLACK_WEBHOOK_URL_PROD --repo martinogie/bank-health-data-platform     # it prompts; paste the URL
gh secret set DISCORD_WEBHOOK_URL_PROD --repo martinogie/bank-health-data-platform
```

### 4. Keep the watchdog switched on

GitHub **disables scheduled workflows in public repositories after 60 days without activity**
(no commits, pull requests or other events). GitHub emails you before it happens. To switch it
back on: GitHub → **Actions** → *Late-run watchdog* → **Enable workflow**. Any commit to the
repository also resets the 60 days. Check it is enabled whenever you come back to the project.

### 5. The watchdog's warehouse and cost

The watchdog logs in as the CI user, whose only warehouse is **`BANK_DEV_WH`**, under the dev
warehouse's monthly cap (`BANK_DEV_WH_MONITOR`).

- **Cost:** one short query each Monday wakes the XSMALL warehouse, which bills at least 60
  seconds: about **0.017 credits a run**, so **about 0.07 credits a month** (4–5 Mondays). Manual
  runs add the same each.
- **If the dev cap is used up,** the warehouse can't start and the watchdog can't read the run
  log. It doesn't fail silently: it alerts **"WATCHDOG: could not check this week's run (the CI
  warehouse's monthly credit cap is used up)"** in both prod channels, and the workflow run
  fails. The prod run itself may be fine: check Airflow.
- **So the dev cap must leave room** for the watchdog: keep about 0.1 credits a month spare
  after dev and CI work. If dev and CI regularly use the whole cap, give the watchdog its own
  headroom instead, for example `GRANT USAGE ON WAREHOUSE BANK_WH TO ROLE CI_ROLE` and point the
  workflow at `BANK_WH`, which only the account cap limits. That's your decision; it isn't set up.

## Test the alerts (dev only, never prod)

1. **The alert path (about a minute).** Start the dev stack, open <http://localhost:8081>, and
   trigger the DAG **`bank_health_alert_test`** (it exists only in dev). Its one task fails on
   purpose. Expected within a minute in **both dev channels**:
   `[DEV] FAILED: bank_health_alert_test / fail_on_purpose`, with the try number, the error
   line, and a link to the log.
2. **A broken real run (acceptance test; a few minutes).** Make the weekly DAG fail early,
   without using Databricks or Snowflake compute: in the dev Airflow, set the FDIC watermark to
   something that isn't a date, then run the DAG once (as in CONTRIBUTING.md, "Run the dev
   pipeline"):
   ```bash
   docker compose exec airflow-scheduler airflow variables set fdic_last_repdte not-a-date
   docker compose exec airflow-scheduler bash -c 'eval "$(python /opt/airflow/include/connections_from_env.py)" && airflow dags test bank_health_weekly'
   ```
   `download_fdic` fails on every try. After its retries (2 × 5 minutes), expect
   `[DEV] FAILED: bank_health_weekly / download_fdic` in both dev channels, and one
   `OPS.PIPELINE_RUNS` row in `BANK_HEALTH_DEV` with `STATE = 'failed'` and
   `FAILED_TASKS = 'download_fdic'`. Put the watermark back afterwards
   (`airflow variables set fdic_last_repdte 2026-06-30`).
3. **The late-run check.** Trigger `bank_health_slo_check` in dev with the configuration
   `{"pretend_now": "2026-11-02T12:05:00-06:00"}`. With no successful dev run logged that week,
   expect `[DEV] LATE: …` in both dev channels.

## What to do when an alert arrives

| Alert | First steps |
|---|---|
| FAILED, any task | Open the **Log** link (through the tunnel: `ssh bank-health-vm`, then the link). Fix the cause, then **clear** the failed task in the UI: Airflow re-runs it and everything after it. Every task is safe to re-run (the DAG's docs list why). |
| … Databricks quota | Don't retry today. [quota_errors.md](quota_errors.md). |
| FAILED check_freshness | Which check? *Not rebuilt:* dbt stopped early; look at the `dbt_build` tasks. *Old FDIC quarter:* the FDIC may be late (check BankFind), or the download missed it. *Scored banks out of range:* look at `05_score` in Databricks. The watchlist was **not** published. |
| FAILED check_volumes | The message names the layer, its value, the median and the threshold. A real cause (e.g. a backfill) is fine: re-run `check_volumes` once you're satisfied. Otherwise find the layer that changed. The watchlist was **not** published. |
| LATE | Is the run still going (Airflow UI)? Did it fail (there would be a FAILED alert)? Is the VM up (`ssh bank-health-vm`)? If the VM is down: [oracle_vm_setup.md](oracle_vm_setup.md); the PC is the rollback until three weeks after cutover. |
| WATCHDOG could not check | GitHub couldn't read Snowflake as the CI user: Snowflake down, the trial or billing, or the CI key. Check the workflow log. |
| No OK message by Monday afternoon | Treat it like LATE. |

## Thresholds

The volume thresholds and their reasons are in `ingestion/volume_checks.py` (cumulative layers:
−0.5% / +5%; scored banks: ±3%; baseline: the median of the last 4 successful runs, needing at
least 3). Review them after about 8 weeks of `OPS.PIPELINE_RUNS` history: if real weeks trip
them, widen them in a pull request, with the evidence.
