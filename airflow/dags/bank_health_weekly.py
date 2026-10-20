"""
### Bank Health — weekly pipeline

Every Monday 06:00 America/Chicago:

1. **download_fdic** — new FDIC call-report quarters (+ last 2 re-pulled for amendments),
   plus full institutions and failures lists.
2. **download_cfpb** — CFPB complaints since the last watermark (30-day look-back),
   credit bureaus excluded.
3. **upload_raw** — push the new files into the Databricks Volume
   `/Volumes/bank_health/bronze/raw_files`.
4. **save_watermarks** — remember how far we got (Airflow Variables), only after upload succeeds.

Later steps (added as the project grows): run the Databricks job (bronze → silver → gold →
model → score), load Snowflake RAW, `dbt build`, `dbt docs generate`.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pendulum
from airflow.sdk import Variable, dag, task

RAW_DIR = Path("/opt/airflow/data/raw")
TZ = "America/Chicago"

# Airflow Variables used as watermarks (visible under Admin -> Variables)
FDIC_WATERMARK = "fdic_last_repdte"       # e.g. "2026-06-30"
CFPB_WATERMARK = "cfpb_last_date_received"  # e.g. "2026-10-18"


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dag(
    dag_id="bank_health_weekly",
    schedule="0 6 * * 1",
    start_date=pendulum.datetime(2026, 10, 1, tz=TZ),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    doc_md=__doc__,
    tags=["bank-health", "fdic", "cfpb"],
)
def bank_health_weekly():

    @task(doc_md="Download new FDIC quarters, institutions and failures to data/raw/fdic.")
    def download_fdic() -> dict:
        from ingestion import fdic

        today = pendulum.now(TZ).date()
        last = _as_date(Variable.get(FDIC_WATERMARK, default=None))
        latest = fdic.latest_financials_quarter()
        quarters = fdic.quarters_to_download(last, latest)
        out = RAW_DIR / "fdic"

        paths = fdic.download_financials(quarters, out)
        paths.append(fdic.download_institutions(out, today))
        paths.append(fdic.download_failures(out, today))
        return {"paths": [str(p) for p in paths], "watermark": latest.isoformat()}

    @task(doc_md="Download CFPB complaints (bureaus excluded) to data/raw/cfpb.")
    def download_cfpb() -> dict:
        from ingestion import cfpb

        today = pendulum.now(TZ).date()
        last = _as_date(Variable.get(CFPB_WATERMARK, default=None))
        paths = cfpb.download_complaints(last, today, RAW_DIR / "cfpb")
        return {
            "paths": [str(p) for p in paths],
            "watermark": (today - timedelta(days=1)).isoformat(),
        }

    @task(doc_md="Upload this run's files to the Databricks Volume, keeping folder layout.")
    def upload_raw(fdic_result: dict, cfpb_result: dict) -> int:
        from ingestion.databricks_files import DatabricksFiles

        paths = [Path(p) for p in fdic_result["paths"] + cfpb_result["paths"]]
        uploaded = DatabricksFiles.from_env().upload_files(paths, RAW_DIR)
        return len(uploaded)

    @task(doc_md="Advance the FDIC and CFPB watermarks once files are safely in Databricks.")
    def save_watermarks(fdic_result: dict, cfpb_result: dict, n_uploaded: int) -> None:
        Variable.set(FDIC_WATERMARK, fdic_result["watermark"])
        Variable.set(CFPB_WATERMARK, cfpb_result["watermark"])

    fdic_result = download_fdic()
    cfpb_result = download_cfpb()
    n_uploaded = upload_raw(fdic_result, cfpb_result)
    save_watermarks(fdic_result, cfpb_result, n_uploaded)


bank_health_weekly()
