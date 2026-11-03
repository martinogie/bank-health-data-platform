# ADR 0008: Monitoring checks and their thresholds

- **Status:** accepted (Phases 2–3, Oct–Nov 2026)
- **Findings:** O-02, O-03, E-01, M-08

## Context

A run can succeed technically and still be wrong: half a download, a broken match, a model whose
inputs have drifted. Each check needs a threshold that normal weeks don't trip, and a clear reading.

## Decision

| Check | Threshold | Effect |
|---|---|---|
| Freshness (`check_freshness`) | marts rebuilt this run; the FDIC quarter expected for today; scored banks in range | fails the run; watchlist not published |
| Rows added to bronze, per source | 0, under 50% or over 3× the usual (median of the last 4 successful weekly runs; a new FDIC quarter may add more) | fails the run (only reported in a backfill) |
| Silver, gold, marts totals | −0.5% / +5% against that median | fails the run |
| Scored banks | ±3% against that median | fails the run |
| Complaint match rate | more than 2 percentage points below that median | fails the run |
| Model drift (`check_model`) | score PSI above 0.25, or 3+ features above 0.25, against the previous 4 quarters | MODEL message; never fails the run |
| Backtest | a judged quarter's top-10% capture more than 10 points below the test result (52.6%) | MODEL message |
| Late run | no successful run by Monday 12:00 CT | LATE alert, from the VM and from GitHub |

Details: [alerts.md](../runbooks/alerts.md), `ingestion/volume_checks.py`,
`databricks/notebooks/_monitoring.py`.

## Why these numbers

- **Medians of the last 4 runs** (needing 3) ignore one odd week; backfill runs are left out.
- **Rows added, not totals:** bronze grows by design; the old total-bronze rule fired on normal
  growth on 2 Nov 2026 and was replaced the same day.
- **PSI 0.10 / 0.25** are the usual "moderate" and "large" lines. Drift against 2001–2019 training is
  reported only: 20 years of change is expected (score PSI 0.323 on 3 Nov 2026), and alerting on it
  would be noise.
- **Monitoring never fails a run:** drift doesn't make this week's scores wrong.

## Consequences

Thresholds are reviewed after about 8 weeks of history; any change needs the evidence in a PR.
