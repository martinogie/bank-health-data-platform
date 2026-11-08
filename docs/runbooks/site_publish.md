# Runbook: the website's weekly data (R2)

The Bank Health site (`finance.martinsogiekpolor.com/bankhealth`) reads its numbers and the dbt
docs from a **private** Cloudflare R2 bucket, `bankhealth-data`, through the router Worker
(`martinogie/portfolio-site`, `workers/sector-router`). Nothing in the bucket is public; the Worker
reads it through an R2 binding.

## How it updates

1. `bank_health_weekly` succeeds. Its last task, `record_run`, emits the Airflow asset
   `bank_health_weekly_success` (only on a fully successful run).
2. That starts `bank_health_publish_site`, which:
   1. reads the latest successful weekly run (not a backfill) and the marts;
   2. builds the five data files for every bank (`ingestion/site_export.py`) and takes the dbt docs
      from `dbt/target`;
   3. validates every file against the JSON Schemas (`ingestion/site_contract/schemas/`);
   4. scans every file for identifiers and secrets (`ingestion/public_scan.py`);
   5. uploads everything to `runs/<run key>/` and checks every object arrived;
   6. switches `current.json` to the new run;
   7. deletes runs beyond the newest 5 (never the current one).
3. The Worker reads `current.json` (cached for 60 seconds) and serves `/bankhealth/data/*` and
   `/bankhealth/dbt/*` from that run.

If any step before 2.6 fails, the site keeps showing the previous run. Slack and Discord get one
alert; the weekly run stays green. A run key looks like `20261109T120012Z-1a2b3c` (start time
UTC, then 6 characters of the Airflow run id).

## Publish by hand (e.g. after a fix)

Airflow UI → `bank_health_publish_site` → Trigger. It publishes the latest successful weekly run;
no Databricks, no weekly run needed.

## Roll back to an earlier run

On the VM, in the scheduler container (the R2 token is in its environment from OCI Vault):

```sh
docker exec airflow-airflow-scheduler-1 python -m ingestion.site_publish list
docker exec airflow-airflow-scheduler-1 python -m ingestion.site_publish rollback <run key>
```

`list` marks the current run. `rollback` only points `current.json` at a run that is still in the
bucket; the site shows it within a minute (the Worker's cache). To undo, roll forward the same way.

## Rotate the R2 token

1. Cloudflare → R2 → Manage R2 API tokens → Create: **Object Read & Write**, **Apply to specific
   buckets only** → `bankhealth-data`, TTL 1 year.
2. OCI Vault `bank-health-vault`: new versions of `prod-r2-access-key-id` and
   `prod-r2-secret-access-key` (the endpoint doesn't change).
3. On the VM: `deploy/vault_secrets.py fetch`, then restart the stack (or wait for the next deploy).
4. Trigger `bank_health_publish_site` once; when it succeeds, delete the old token in Cloudflare.
5. Update `set_on` for the token in `ops/credentials.yml` (pull request).

Dev keeps its own copy in the dev `.env` (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`)
and only ever writes under `dev/` in the bucket, which the site never serves.

## Deploying the website code (pages, not data)

After a merge to `main`, the **Site deploy** workflow starts once **CI** passes. Its first job checks
whether the merge changed website files (`site/`, the identifier scanner, the site exporter or the
data contract). If yes, the deploy waits for Martins' approval (environment `production`), then
builds and publishes to the Pages project `bankhealth`. If no, nothing is deployed and no approval is
asked. To deploy by hand: Actions → Site deploy → Run workflow (still needs approval).

It follows CI, not the Site workflow, because GitHub sometimes never started the path-filtered
Site run after a merge (#62, #63, #65), which left the deploy skipped.

## Rotate the Cloudflare deploy token

1. Cloudflare → My Profile → API Tokens → Create Custom token `github-deploy-sites` (no expiry;
   rotated yearly): Account (Martins' account only) — Cloudflare Pages: Edit, Workers Scripts: Edit;
   Zone (`martinsogiekpolor.com` only) — Workers Routes: Edit. Nothing else: add a permission only if
   a deploy fails with an authentication error that names it.
2. Replace `CLOUDFLARE_API_TOKEN` in GitHub: `bank-health-data-platform` → Environments →
   production, and `portfolio-site` → repository secrets.
3. Run each deploy workflow once; when both succeed, delete the old token.
4. Update `set_on` in `ops/credentials.yml`.

## Costs

R2, Pages and Workers are used within Cloudflare's free tier (R2: 10 GB stored, 1 million writes and
10 million reads a month, free egress). One weekly run is about 4,300 files (~15 MB); 5 runs kept is
well under 100 MB.
