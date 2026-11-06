# Validation pack: bank distress early warning (v5)

For an **independent validator**: a person Martins chooses, who did not build the model. This pack
says what to check, gives the evidence, and has a report template. *Portfolio model on public
data, not a supervisory rating.* No AI review counts as validation; only a person's report does.

## Scope

| In scope | Out of scope |
|---|---|
| `bank_health.ml.distress_model` v5 (`@champion` since 25 Oct 2026): label, features, split, performance, calibration, bands, reasons, monitoring, promotion rule | the pipeline's infrastructure (covered by tests and runbooks), the Power BI report, complaint matching (not a model input) |
| The data the model sees: `gold.bank_quarter` (FDIC call reports 2000–2026, FDIC failures list) | data the FDIC itself got wrong (taken as reported; see D-05 in the model card) |

What the validator gets: read access to the repository (public), the MLflow runs of v5, and the
documents below. Asking Martins for a read-only Databricks seat is optional; step 1 needs only the
script's output, which Martins can run and send.

## The documents

- [Model card](../model_card.md): what v5 is, the numbers, the known limits.
- [checks.md](checks.md): the 20 checks, each with where the evidence is and what passes.
- [report_template.md](report_template.md): the report to fill in.
- Code: `databricks/notebooks/04_train_model.py` (training, bands, registration), `05_score.py`,
  `06_model_report.py` (threshold-rule comparison, bootstrap intervals), `07_monitor.py`,
  `_features.py`, `_scoring_rules.py`, `_promotion.py`.
- Investigations: [model_credibility.md](../investigations/model_credibility.md),
  [m05_reason_examples.md](../investigations/m05_reason_examples.md),
  [m08_monitoring_dev.md](../investigations/m08_monitoring_dev.md).

## Reproduce

**Step 1: do the published numbers match the records?** (read-only, no compute, seconds)

```powershell
.venv\Scripts\python scripts/validation_reproduce.py
```

It reads `@champion` from Unity Catalog and compares the model card's test results with the
training run's logged metrics, and the band cut-offs with the model version's tags (what scoring
uses). Expected values: [expected_v5.json](expected_v5.json). Result on 6 Nov 2026: **all figures
match** (version 5, run `2afc6dbeeafd4dc6b28f1d5dc82c635f`).

**Step 2: retrain and compare** (optional; one dev Databricks run, counts toward the daily quota).
In a dev checkout: `scripts/databricks_run.py 04_train_model 06_model_report` against
`bank_health_dev`. Do not pass `promote=true`. The new run's test metrics should be within the
step 1 tolerances of v5's (same data, same seeds). A difference is a finding, not a failure of
the validator.

## Open points the validator should look at

| # | Point | Found |
|---|---|---|
| VF-01 | The v5 training run's logged metrics `band_cutoff_*` (0.358 / 0.0633 / 0.0558) differ from the cut-offs on the model version's tags (0.358027 / 0.162898 / 0.035561). Scoring reads the tags, which match the model card, so the bands in production are the documented ones. The same code writes both from one variable, so how the run's values came to differ is not known; the record should be explained or corrected. | 6 Nov 2026, by `validation_reproduce.py` |
| VF-02 | Assistance transactions in the FDIC failures list count as failures (model card, known limits). | Phase 3 |
| VF-03 | Weak for banks over $100B (ROC-AUC 0.666); CBLR banks score less well (no CBLR rows in training). | model card |

## How the review runs

1. Martins sends the validator this folder's link and the step 1 output.
2. The validator works through [checks.md](checks.md) and fills in the
   [report template](report_template.md), with a rating per check and findings.
3. Findings go into the model card (known limits) or the v6 list; the report is saved as
   `docs/validation/report_<date>.md`, signed with the validator's name.
4. The yearly re-review follows [../model_review_checklist.md](../model_review_checklist.md).
