# Model roadmap

Where the bank distress early-warning model is and what comes next. The evidence and approach for
each item are in the [model card](model_card.md).
*Portfolio model on public data, not a supervisory rating.*

| Version | Status | Goal |
|---|---|---|
| **v5** | Live (`@champion` since 25 Oct 2026) | Fixes from the credibility review: no risk-based capital feature, foreign offices and new banks not scored, fixed-meaning bands, release checks |
| **v6** | Planned | A sharper definition of distress and a time-to-distress model |
| **v7** | Planned, after v6 | Test whether CFPB complaint features add predictive value |

## v5 (live)

- **Model:** gradient boosting on FDIC call-report features, trained 2001–2019 and tested
  2020–2024.
- **Test results:** ROC-AUC 0.8515, PR-AUC 0.333 against a 5.5% base rate.
- **Bands:** High ≥ 50%, Elevated 25–50%, Watch 10–25%, Low < 10% distress rate within the band.

## v6: sharper distress definition and a time-to-distress model

- **What it is:** refine what counts as distress, fix the earnings and young-bank distortions
  found in the v5 review, and add a hazard model for timing.
- **How it's released and judged:** one release, compared with v5 on the same 2020–2024 test
  rows.

| Order | Item |
|---|---|
| 1 | 6.1 Count losses toward distress only when they are material |
| 2 | 6.3 Handle young and recently merged banks |
| 3 | 6.2 Separate realised securities gains and losses from operating earnings |
| 4 | 6.6 Time-to-distress hazard model (needs 6.1 and 6.3) |
| 5 | 6.5 Calibrate the classifier's probabilities |
| 6 | 6.4 Report credit risk and failure risk as separate outputs (needs 6.6) |

**Why this order:**
1. 6.1 and 6.3 settle which bank-quarters count as distressed and which banks are scored.
   Everything else is trained on that definition.
2. 6.2 adds a data field and the features built from it.
3. The hazard model comes next, once the definition is settled.
4. Calibration is fitted on the final classifier.
5. The separate outputs need the hazard model's failure-only variant.

## v7: complaint features, tested

- **What it tests:** whether CFPB complaint features add predictive value. They are adopted only
  if a fair comparison shows a clear gain.
- **When:** starts after v6 is released.

| Order | Item |
|---|---|
| 1 | 7.1 Complaint features as a tested challenger |

**7.1 in brief:**
1. Back-fill complaints from late 2011, with the same entity resolution as today.
2. Build features: complaints per $1bn of deposits, change over 4 quarters, share about account
   management or fees, share with untimely responses.
3. Normalise for database growth and for products added over time.
4. Train a v6-based model with and without the features, on the same split.
5. Report the results overall and for large banks, then record the adopt or reject decision.
   Calibration must not get worse.

## How a version is released

1. **Train:** the new version is registered as `@candidate`.
2. **Compare:** it is compared with the current champion on the same test rows.
3. **Review:** a person reviews the top 20 ([runbook](runbook.md)).
4. **Promote:** it becomes `@champion` only after approval.
5. **Roll back if needed:** the runbook covers it.
