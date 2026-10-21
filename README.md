# Bank Health Data Platform

**Which of the banks you depend on should you worry about this quarter, and why?**

Regulators rate every US bank, but those ratings are confidential. Everyone else with money at
risk at a bank has to work it out from public filings:

- a **fintech** whose customer accounts sit at one small sponsor bank,
- a **company treasurer** holding deposits above the $250,000 FDIC limit,
- a **bank** with loan participations or correspondent accounts at other banks.

When Silicon Valley Bank failed in March 2023, and when Synapse collapsed in 2024 and froze its
fintech customers' funds, those were the people caught out.

This project is an early-warning system for them, built only from public data. Every quarter it
scores every FDIC-insured bank (4,257 in 2026 Q2) on its chance of distress in the next 12 months.
It gives each bank's top three reasons and compares it with banks of the same size. A
**watchlist** lets you enter the banks you deal with and see their scores, trends and reasons on
one page.

**Try the watchlist:** [martinogie.github.io/bank-health-data-platform/watchlist.html](https://martinogie.github.io/bank-health-data-platform/watchlist.html)

It runs on 26 years of FDIC call reports for 27,835 banks and nearly 2 million CFPB consumer
complaints, through a full production-style pipeline.

> A screening tool on public data, not a supervisory rating. Use it to decide which banks to look
> at more closely, not to judge any bank.

## What it answers

- Which of the banks I depend on should I worry about this quarter, and why?
- How healthy is the US banking system right now, and how has it changed since 2000?
- Which large banks get the most consumer complaints for their size?
- Which banks are most likely to enter distress in the next 12 months, and why?
- For any one bank: are its bad loans, capital and profits keeping up with banks of the same size?

## Architecture

```mermaid
flowchart LR
    A[FDIC BankFind API<br/>financials, institutions, failures] --> AF
    B[CFPB Complaint API<br/>complaints since Oct 2023] --> AF
    AF[Apache Airflow in Docker<br/>weekly DAG, Mondays 06:00 CT] -->|upload raw files| V[(Databricks<br/>Unity Catalog volume)]
    AF -->|run job| DB
    subgraph DB[Databricks, PySpark]
      BR[bronze<br/>raw Delta] --> SI[silver<br/>typed, de-duplicated,<br/>complaint company to FDIC bank] --> GO[gold<br/>one row per bank per quarter]
      GO --> ML[MLflow<br/>train + register model] --> SC[score latest quarter]
    end
    V --> BR
    GO -->|load| SF[(Snowflake RAW)]
    SC -->|load| SF
    SF --> DBT[dbt<br/>staging, intermediate, marts<br/>89 data tests]
    DBT --> M[(Snowflake MARTS)]
    M -->|Import, key-pair login| PBI[Power BI report]
```

Why Airflow downloads the data instead of Databricks: Databricks Free Edition only allows outbound
internet to a short list of trusted domains, so Airflow fetches the files and pushes them into a Unity
Catalog volume.

| Step | Tool | What happens |
|---|---|---|
| Ingest | Airflow + Python (`ingestion/`) | Pages through the FDIC and CFPB APIs. Watermarks mean only new quarters and new complaint dates are fetched after the first run. |
| Bronze / silver / gold | Databricks notebooks (`databricks/notebooks/`) | PySpark turns raw files into Delta tables, removes duplicates, matches complaint companies to FDIC banks and builds about 40 columns of ratios and growth rates per bank-quarter. |
| Model | Databricks + MLflow | Gradient-boosted classifier, tracked and registered in Unity Catalog as `bank_health.ml.distress_model@champion`. |
| Warehouse | Snowflake | `RAW`, `STAGING` and `MARTS` schemas, an XSMALL warehouse that suspends after 60 s, a 20-credit monitor, key-pair service users. |
| Transform + test | dbt (`dbt/`) via astronomer-cosmos | 12 models and 89 data tests, one Airflow task per model. |
| Report | Power BI (`powerbi/`) | Three pages, built from code as a Power BI project (`build_report.py`). |

## Results from the first full run (20 Oct 2026)

| | |
|---|---|
| FDIC history | 106 quarters, 2000 Q1 to 2026 Q2; 743,802 bank-quarters; 4,117 bank failures |
| CFPB complaints | 1,969,948 (1 Oct 2023 to 19 Oct 2026, credit bureaus excluded) |
| Complaints matched to an FDIC bank | 30.0% (591,795) from 142 companies. The rest are mostly debt collectors, fintechs and credit unions, which are not FDIC banks. |
| dbt | 12 models built; 89 tests: 87 passed, 2 warned (real outliers in FDIC data), 0 errors; 5 of 5 freshness checks passed |
| Snowflake cost | about 0.2 credits for the full run |
| Spot checks against the live APIs | JPMorgan Chase total assets Q2 2026, Wells Fargo Q4 2008, complaint counts for Jan 2025 and Jul 2026, failed-bank list: all exact matches |

### Distress model

A bank-quarter is labelled **distressed** if, within the next 4 quarters, the bank fails, loses money two
quarters in a row, has non-current loans above 3%, or has Tier 1 leverage below 5%. Training uses
2001–2019 and the model is tested once on 2020–2024, so it never sees the future.

| Test period 2020–2024 | Gradient boosting (champion v4) | Logistic regression baseline |
|---|---|---|
| ROC-AUC | **0.854** | 0.742 |
| PR-AUC | **0.357** (6.4× the 5.6% base rate) | 0.222 |
| Distress cases caught in the riskiest 10% | **53.5%** | 39.6% |

Scores for 2026 Q2 (4,257 banks): High 195, Elevated 388, Watch 971, Low 2,330, Already in distress 373.
Each bank gets its top three reasons, found by resetting one feature at a time to the median and
measuring how much the probability falls.

> This is a portfolio model on public data. It is not a supervisory rating and should not be used to
> judge any real bank.

## Watchlist

[`watchlist.html`](https://martinogie.github.io/bank-health-data-platform/watchlist.html) answers the
question for one organisation's own banks. Search by name, city, state or FDIC certificate number
and add the banks you depend on. Each card shows:

- the risk band, the probability of distress in the next 12 months and the percentile against banks
  of the same size,
- the top three reasons in plain words,
- what changed since last quarter, and 8-quarter trend lines for non-current loans, Tier 1 leverage,
  ROA, efficiency ratio and deposit growth, each against the median of same-size banks,
- consumer complaints, for banks matched to the CFPB data.

Banks under 3 years old are labelled *"new bank: ratios distorted by start-up costs"* (review finding
M-02, until the model handles start-up losses). The list is kept in the browser and in the link
(`watchlist.html?certs=628,3511`), so it can be shared, and exported to CSV. Plain HTML, CSS and
JavaScript with no build step and no libraries.

| Desktop | Phone (375 px) |
|---|---|
| ![Watchlist on desktop](docs/images/watchlist_desktop.png) | ![Watchlist at phone width](docs/images/watchlist_phone.png) |

The data is `site/data/watchlist.json` (schema in [`site/data/README.md`](site/data/README.md)): one
record per scored bank, exported from Snowflake `MARTS` by `scripts/export_watchlist_data.py` and by
the DAG's final task, `export_watchlist`, on every run.

## Power BI report

| Overview | Early warning | Bank detail (drill-through) |
|---|---|---|
| ![Overview](powerbi/screenshots/overview.png) | ![Early warning](powerbi/screenshots/early_warning.png) | ![Bank detail for Synchrony Bank](powerbi/screenshots/bank_detail.png) |

- **Overview:** six KPI cards with the change from the previous quarter, the share of banks with bad
  loans above 3% since 2000, the number of banks left, complaint rates for large banks, banks by size.
- **Early warning:** counts by risk band, a sortable table of every bank's probability and reasons,
  risk versus complaints.
- **Bank detail:** right-click any bank → Drill through. The bank's ratios against the median of banks
  the same size, the reasons behind its score, and what customers complain about.

Design: white page with a teal top bar and page tabs, flat white cards, question-style titles, teal
for good news and orange for bad news on the change lines.

The report is stored as a Power BI project (`.pbip`, TMDL model + PBIR pages), so every measure and
visual is readable text in git. `powerbi/build_report.py` generates the whole thing (model, 60+ DAX
measures, relationships, theme and the three pages), so the report can be rebuilt from code. Power BI connects to Snowflake
in Import mode as `SVC_POWERBI`, a read-only service user with key-pair login
(`snowflake/powerbi_user.sql`).

## Testing

Five kinds of tests, each in code:

| Type | What it checks | Where | Latest result |
|---|---|---|---|
| **Unit** | One function on its own. HTTP sessions, files and clients are replaced with fakes (`FakeSession`, `monkeypatch`, `tmp_path`), so the tests run offline in seconds and never hit a real API. Covers paging, date windows, watermarks, file naming, retry logic and the watchlist export (JSON schema, rounding, new-bank flag, 8-quarter ordering). | `ingestion/tests/` | 110 passed |
| **Integration** | Real pieces together: the live FDIC and CFPB APIs, a Databricks Files API upload and list, Snowflake key-pair login and a small `write_pandas` load. Skipped when credentials are missing. | `tests/integration/` (10 tests) | run on demand with credentials |
| **End-to-end** | The whole pipeline after a DAG run: Airflow run state, Snowflake RAW and MARTS row counts, freshness, and numbers checked against the live sources. | `tests/e2e/` | 17 passed (20 Oct 2026 run) |
| **Data quality** | The data itself: unique and not-null keys, relationships, accepted values, value ranges, source freshness. | dbt tests in `dbt/models/**/_*.yml` (`dbt_utils`, `dbt_expectations`) and checks in the silver notebook | 87 passed, 2 warned, 0 errors |
| **Smoke** | Does it start at all: Airflow containers healthy, the API server answers, the DAG imports with no errors. | `tests/smoke/` | 7 passed |
| **Browser smoke** | The watchlist page in Chromium (Playwright): add 3 banks, reload and the list persists; a `?certs=` link renders in a fresh browser; sort order; new-bank label; works with localStorage blocked; no sideways scroll at 375 px. | `tests/browser/` | 7 passed |

How to run them:

```bash
pytest                                        # unit tests (offline)
pytest tests/integration -m integration       # needs .env credentials
pytest tests/smoke -m smoke                   # needs the Airflow stack running
pytest tests/e2e -m e2e                       # after a full DAG run
python -m playwright install chromium         # once
pytest tests/browser -m browser               # watchlist page in a real browser
cd dbt && dbt build                           # models + data tests
```

## Run it yourself

1. **Accounts:** Databricks Free Edition, a Snowflake trial and Docker Desktop. Copy `.env.example`
   to `.env` and fill it in. Secrets stay in `.env` and `keys/`, which git ignores.
2. **Snowflake:** run `snowflake/setup.sql` as ACCOUNTADMIN, then attach your public key to
   `SVC_BANK_PIPELINE`.
3. **Databricks:** link this repo as a Git folder and run `00_setup` once.
4. **Airflow:** `cd airflow && docker compose up -d`, open http://localhost:8080, then trigger
   `bank_health_weekly`. The first run loads the full history; later runs only add new data.
5. **Power BI:** run `snowflake/powerbi_user.sql`, open `powerbi/BankHealth.pbip`, sign in with
   **Key pair** as `SVC_POWERBI`, then click Refresh. Details are in `powerbi/DASHBOARD_GUIDE.md`.
6. **Watchlist:** the DAG refreshes `site/data/watchlist.json` on every run (or run
   `python scripts/export_watchlist_data.py`). Preview with `python -m http.server` and open
   http://localhost:8000/watchlist.html; commit the JSON to publish it on GitHub Pages.

## Repository layout

```
airflow/      Docker Compose stack, custom image, DAG bank_health_weekly
ingestion/    FDIC + CFPB clients, Databricks upload, Snowflake load (+ unit tests)
databricks/   notebooks 00_setup to 05_score (PySpark, MLflow)
snowflake/    setup.sql, powerbi_user.sql
dbt/          staging, intermediate and mart models with tests
powerbi/      BankHealth.pbip (TMDL + PBIR), build_report.py, theme, screenshots
watchlist.html  the watchlist page (plain HTML/CSS/JS); index.html is the project page
site/data/    watchlist.json + its schema (README.md)
scripts/      export_watchlist_data.py and helper scripts
tests/        integration, smoke, browser and end-to-end tests
docs/         workflow PDF, end-to-end run report, screenshots
```

## Data sources

- FDIC BankFind Suite API, public domain: <https://api.fdic.gov/banks/>
- CFPB Consumer Complaint Database, CC0: <https://www.consumerfinance.gov/data-research/consumer-complaints/>

---

Built by **Martins Ogiekpolor** · [github.com/martinogie](https://github.com/martinogie) · Built with
AI-assisted coding; the design, analysis and review are my own. MIT licence.
