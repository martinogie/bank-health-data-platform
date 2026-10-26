"""Which environment this process runs in (dev, ci or prod), and what that environment may touch.

Every component reads BANK_HEALTH_ENV through this module: the Airflow DAG, the loaders and
exporters, the scripts, dbt (profiles.yml target) and the Databricks job (the `env` parameter).
There is no default. If BANK_HEALTH_ENV is unset or not exactly dev, ci or prod, everything
stops with a clear error instead of guessing.

| env  | Snowflake database             | Databricks catalog | May write prod, publish, move @champion |
|------|--------------------------------|--------------------|-----------------------------------------|
| prod | BANK_HEALTH                    | bank_health        | yes                                     |
| dev  | BANK_HEALTH_DEV                | bank_health_dev    | no                                      |
| ci   | BANK_HEALTH_DEV (CI_<run_id>)  | none               | no                                      |

The guards below are the code half of the safety net; the Snowflake grants in
snowflake/setup_dev.sql are the other half (DEV_ROLE and CI_ROLE cannot write BANK_HEALTH).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_VAR = "BANK_HEALTH_ENV"
ENVIRONMENTS = ("dev", "ci", "prod")

PROD_DATABASE = "BANK_HEALTH"
PROD_CATALOG = "bank_health"


class EnvironmentNotSet(RuntimeError):
    """BANK_HEALTH_ENV is missing or not one of dev, ci, prod."""


class EnvironmentMismatch(RuntimeError):
    """A setting (database, catalog) belongs to a different environment than BANK_HEALTH_ENV."""


class ProdWriteRefused(PermissionError):
    """A non-prod environment tried to write a prod object, publish, or move @champion."""


@dataclass(frozen=True)
class Settings:
    name: str
    snowflake_database: str
    databricks_catalog: str | None
    databricks_job_name: str | None
    # Workspace folder the job's notebooks run from, under /Users/<me>/. Prod: the Git folder
    # (pulled to main). Dev: a plain folder the local notebooks are uploaded to, so it can never
    # be mistaken for the prod Git folder.
    databricks_folder: str | None
    dbt_target: str
    mlflow_experiment: str | None

    @property
    def is_prod(self) -> bool:
        return self.name == "prod"


SETTINGS = {
    "prod": Settings(
        name="prod",
        snowflake_database=PROD_DATABASE,
        databricks_catalog=PROD_CATALOG,
        databricks_job_name="bank_health_pipeline",
        databricks_folder="bank-health-data-platform",
        dbt_target="prod",
        mlflow_experiment="bank_health_distress_model",
    ),
    "dev": Settings(
        name="dev",
        snowflake_database="BANK_HEALTH_DEV",
        databricks_catalog="bank_health_dev",
        databricks_job_name="bank_health_pipeline_dev",
        databricks_folder="bank-health-dev-run",
        dbt_target="dev",
        mlflow_experiment="bank_health_distress_model_dev",
    ),
    "ci": Settings(
        name="ci",
        snowflake_database="BANK_HEALTH_DEV",
        databricks_catalog=None,
        databricks_job_name=None,
        databricks_folder=None,
        dbt_target="ci",
        mlflow_experiment=None,
    ),
}


def current_env() -> str:
    """dev, ci or prod, from BANK_HEALTH_ENV. Raises EnvironmentNotSet otherwise (no default)."""
    raw = os.environ.get(ENV_VAR)
    if raw is None or not raw.strip():
        raise EnvironmentNotSet(
            f"{ENV_VAR} is not set. Set it to one of {', '.join(ENVIRONMENTS)} "
            "(in .env for scripts, in the Compose file for Airflow, in the workflow for CI). "
            "There is deliberately no default."
        )
    env = raw.strip()
    if env not in ENVIRONMENTS:
        raise EnvironmentNotSet(
            f"{ENV_VAR}={raw!r} is not valid. Use exactly one of {', '.join(ENVIRONMENTS)}."
        )
    return env


def settings() -> Settings:
    """Settings for the current environment, after checking that the .env values agree with it."""
    s = SETTINGS[current_env()]
    database = os.environ.get("SNOWFLAKE_DATABASE", "").strip().strip('"')
    if database and database.upper() != s.snowflake_database:
        raise EnvironmentMismatch(
            f"{ENV_VAR}={s.name} expects SNOWFLAKE_DATABASE={s.snowflake_database}, "
            f"but it is {database}. Check that this .env belongs to the {s.name} environment."
        )
    catalog = os.environ.get("DATABRICKS_CATALOG", "").strip()
    if catalog and catalog != s.databricks_catalog:
        raise EnvironmentMismatch(
            f"{ENV_VAR}={s.name} expects the Databricks catalog {s.databricks_catalog}, "
            f"but DATABRICKS_CATALOG is {catalog}. Remove DATABRICKS_CATALOG from .env "
            "(the catalog now follows the environment)."
        )
    return s


def databricks_catalog() -> str:
    """The Unity Catalog catalog of the current environment (raises in ci, which has none)."""
    s = settings()
    if s.databricks_catalog is None:
        raise EnvironmentMismatch(f"{ENV_VAR}={s.name} has no Databricks catalog")
    return s.databricks_catalog


def is_prod_object(name: str) -> bool:
    """True for the prod Snowflake database or Databricks catalog, or anything inside them.

    Accepts BANK_HEALTH, BANK_HEALTH.RAW.X, "BANK_HEALTH".RAW, bank_health, bank_health.ml.x,
    /Volumes/bank_health/... Matching is on the first name part, so BANK_HEALTH_DEV is not prod.
    """
    text = name.strip()
    if text.startswith("/Volumes/"):
        text = text[len("/Volumes/") :].replace("/", ".")
    first = text.split(".", 1)[0].strip().strip('"')
    return first.upper() == PROD_DATABASE  # same spelling for the database and the catalog


def assert_can_write(target: str) -> None:
    """Raise ProdWriteRefused if a non-prod environment is about to write a prod object."""
    env = current_env()
    if env != "prod" and is_prod_object(target):
        raise ProdWriteRefused(f"{ENV_VAR}={env} may not write {target}: only prod writes prod objects.")


def assert_can_publish(what: str) -> None:
    """Only prod publishes (watchlist, site data, the dbt manifest CI compares against)."""
    env = current_env()
    if env != "prod":
        raise ProdWriteRefused(f"{ENV_VAR}={env} may not publish {what}: only prod publishes.")


def assert_can_promote(model: str) -> None:
    """Only prod moves the @champion alias."""
    env = current_env()
    if env != "prod":
        raise ProdWriteRefused(f"{ENV_VAR}={env} may not move {model}@champion: only prod promotes.")


def describe() -> str:
    """One line for logs. Names only, never credentials."""
    s = settings()
    return (
        f"environment={s.name} snowflake_database={s.snowflake_database} "
        f"databricks_catalog={s.databricks_catalog} databricks_job={s.databricks_job_name} "
        f"dbt_target={s.dbt_target}"
    )
