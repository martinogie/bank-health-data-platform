# Custom Airflow image for the Bank Health pipeline.
FROM apache/airflow:3.3.2-python3.12

ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.12
ARG CONSTRAINTS=https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt

# 1. Providers + libraries, pinned by Airflow's tested constraints file
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
        -r /tmp/requirements.txt --constraint "${CONSTRAINTS}"

# 2. dbt in its own virtualenv so its dependencies can't clash with Airflow's.
#    Cosmos is pointed at /home/airflow/dbt_venv/bin/dbt.
RUN python -m venv /home/airflow/dbt_venv \
 && /home/airflow/dbt_venv/bin/pip install --no-cache-dir dbt-core==1.12.6 dbt-snowflake==1.12.3

# 3. Startup wrapper that turns plain .env values into Airflow connections
COPY --chmod=755 include/entrypoint.sh /opt/airflow/include/entrypoint.sh
COPY include/connections_from_env.py /opt/airflow/include/connections_from_env.py

ENV PYTHONPATH=/opt/airflow \
    DBT_EXECUTABLE=/home/airflow/dbt_venv/bin/dbt

ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/airflow/include/entrypoint.sh"]
