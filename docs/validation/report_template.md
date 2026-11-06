# Validation report: bank distress early warning, version ___

Save as `docs/validation/report_<yyyy-mm-dd>.md`.

| | |
|---|---|
| Validator (name, role) | |
| Independence | did not build or train this model: yes / no (explain) |
| Date of review | |
| Model version and alias | `bank_health.ml.distress_model` v___ (`@champion`) |
| Step 1 output (`validation_reproduce.py`) | attached / pasted below; result: |
| Step 2 retrain done | yes / no; if yes, the dev run ID and the differences |

## Overall opinion

One of: **Fit for its stated use** / **Fit with findings to address** / **Not fit**. Stated use:
ranking US banks by the chance of entering distress within 4 quarters, from public data, as a
portfolio project, not a supervisory rating.

Opinion: ______

Summary (3–5 sentences): ______

## Checks

| # | Check | Rating (Pass / Pass with finding / Fail) | Notes |
|---|---|---|---|
| 1 | Label matches its definition | | |
| 2 | Already-distressed banks not scored as entering | | |
| 3 | Exclusions justified and counted | | |
| 4 | Missing is not zero | | |
| 5 | Outliers handled deliberately | | |
| 6 | Features use only past information | | |
| 7 | Out-of-time split, test used once | | |
| 8 | Incomplete outcomes not trained on | | |
| 9 | Published results are the recorded ones | | |
| 10 | Beats a simple rule, clear and stable | | |
| 11 | Chosen on validation, not test | | |
| 12 | Results by segment; weak ones disclosed | | |
| 13 | Calibration measured and disclosed | | |
| 14 | Band cut-offs out of sample, training years | | |
| 15 | Scoring uses the version's cut-offs (VF-01) | | |
| 16 | Reasons faithful and readable | | |
| 17 | Monitoring thresholds reach a person | | |
| 18 | Promotion gate | | |
| 19 | Pinned environment | | |
| 20 | Release checks | | |

## Findings

| ID | Finding | Severity (high / medium / low) | Recommendation | Owner's response (Martins) |
|---|---|---|---|---|
| | | | | |

## Sign-off

Validator: ______ (name), ______ (date)

Model owner's acknowledgement: Martins Ogiekpolor, ______ (date)
