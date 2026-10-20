"""Print `export AIRFLOW_CONN_...=...` lines built from plain .env variables.

entrypoint.sh evals the output, so Airflow sees connections `databricks_default`
and `snowflake_default` without secrets being typed twice or stored in the
Airflow database. Output goes only to the shell's eval, never to logs.
"""
import json
import os
import shlex


def env(name: str) -> str:
    return os.environ.get(name, "").strip()


def main() -> None:
    conns = {}

    if env("DATABRICKS_HOST") and env("DATABRICKS_TOKEN"):
        conns["DATABRICKS_DEFAULT"] = {
            "conn_type": "databricks",
            "host": env("DATABRICKS_HOST"),
            "password": env("DATABRICKS_TOKEN"),  # personal access token
        }

    if env("SNOWFLAKE_ACCOUNT") and env("SNOWFLAKE_PRIVATE_KEY_PATH"):
        extra = {
            "account": env("SNOWFLAKE_ACCOUNT"),
            "warehouse": env("SNOWFLAKE_WAREHOUSE"),
            "database": env("SNOWFLAKE_DATABASE"),
            "role": env("SNOWFLAKE_ROLE"),
            "private_key_file": env("SNOWFLAKE_PRIVATE_KEY_PATH"),
        }
        conn = {
            "conn_type": "snowflake",
            "login": env("SNOWFLAKE_USER"),
            "schema": "RAW",
            "extra": extra,
        }
        if env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            conn["password"] = env("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")  # key passphrase
        conns["SNOWFLAKE_DEFAULT"] = conn

    for name, value in conns.items():
        print(f"export AIRFLOW_CONN_{name}={shlex.quote(json.dumps(value))}")


if __name__ == "__main__":
    main()
