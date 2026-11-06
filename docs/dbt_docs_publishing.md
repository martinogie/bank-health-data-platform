# dbt docs: built, checked, not published

Decision 6 (3 Nov 2026): the dbt docs (models, columns, tests, lineage) are public, but published
only together with the website, at `bank.martinsogiekpolor.com/dbt`. **Nothing is published yet.**

## Build (no database connection, no data)

From the repo root, with the Airflow image (it has dbt 1.12.6 and dbt-snowflake 1.12.4):

```bash
docker run --rm --entrypoint bash -v "$PWD/dbt:/src:ro" -v "$PWD/site_build:/out" \
  -e BANK_HEALTH_ENV=prod -e SNOWFLAKE_ACCOUNT=placeholder -e SNOWFLAKE_USER=placeholder \
  -e SNOWFLAKE_ROLE=placeholder -e SNOWFLAKE_WAREHOUSE=placeholder \
  -e SNOWFLAKE_PRIVATE_KEY_PATH=/nonexistent -e DO_NOT_TRACK=1 \
  bank-health-airflow:3.3.2 -c 'cp -r /src /tmp/dbt && cd /tmp/dbt && D=/home/airflow/dbt_venv/bin/dbt &&
    $D deps --profiles-dir . && $D parse --profiles-dir . &&
    $D docs generate --profiles-dir . --no-compile --empty-catalog --static &&
    cp target/static_index.html /out/index.html'
```

Why these flags:
- `--no-compile` + `parse` only: no query reaches Snowflake; the docs show each model's source SQL.
- `--empty-catalog`: no table statistics (row counts, sizes), so no data at all.
- Placeholder connection values: the real account name can't end up in the files.
- `DO_NOT_TRACK=1`: no anonymous user ID in the manifest (`"user_id": null`).

## Check before publishing (required)

```powershell
.venv\Scripts\python scripts/check_public_docs.py site_build/index.html
```

Exit 0 is required. It checks generic patterns (email addresses, IP addresses, OCI IDs, Snowflake
and Databricks hostnames, private keys, home paths, AWS keys) and the real account values from
`.env` without printing them.

Result on 6 Nov 2026 (dbt project at main after PR 8): one file, 3.5 MB, documenting 12 models,
5 sources and 99 tests (unique IDs counted in the file; e.g. `fct_bank_quarter`,
`mart_early_warning`); scan **clean**; `"user_id": null`.

## Publish (later, with the website)

Copy `index.html` to the website's `/dbt/` path in the site's own repository and deploy it with
the site. Re-run the build and the check on that day; a docs page from an old build must not go
out.
