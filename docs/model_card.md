# Model card: bank distress early warning

`bank_health.ml.distress_model` in Unity Catalog. *Portfolio model on public data, not a
supervisory rating.*

## Version 5 (registered 2026-10-25; reviewed and promoted to `@champion` the same day)

**What it predicts.** The probability that a bank that is not in distress today enters distress
or fails within the next 4 quarters. Distress = non-current loans > 3% of loans, Tier 1 leverage
< 5%, or a net loss in two consecutive quarters. Unit: one bank in one quarter.

**Data.** FDIC call reports 2000–2026 (`gold.bank_quarter`) and the FDIC failures list. Features
use only that quarter's call report and earlier ones. **The model uses FDIC call-report features
only; CFPB complaints are shown as context and are not model inputs.**

**Model.** scikit-learn 1.5.2 `HistGradientBoostingClassifier`, the same settings as v4. It was
chosen over a logistic-regression baseline on 2017–2019 validation PR-AUC (0.343 vs 0.236).
31 features: capital (Tier 1 leverage, equity to assets), asset quality, earnings, liquidity,
growth, loan mix, size, and 1- and 4-quarter changes.

**Split.** Same as v4: train 2001–2019, test 2020–2024 (out of time, used once).
**v5 is compared with v4 on the same test period and the same test rows.**

**Environment.** Trained and scored in Databricks serverless **environment version 2** (Python
3.11, numpy 1.23, with mlflow 2.22.5 and scikit-learn 1.5.2 installed). The job and the manual
run script pin this version (`ENVIRONMENT_VERSION` in `ingestion/databricks_jobs.py`).
`04_train_model` and `05_score` stop if they run anywhere else. v4 was saved under environment 4
(numpy 2) and only loads there, so a rollback to v4 means changing the pin too.

### What changed from v4 (fixes only)

| Change | Why |
|---|---|
| Total risk-based capital removed from the features (Tier 1 leverage kept); the ratio stays in the data for display | From 2020, banks using the Community Bank Leverage Ratio (CBLR) don't report it, and FDIC data shows 0 (42.6% of banks in 2026 Q2). Every CBLR bank reports Tier 1 leverage (lowest 1%: 8.38%). |
| US offices of foreign banks excluded; band "Office of a foreign bank – not scored" | They report no capital ratios (FDIC shows 0), which read as "already in distress". Identified by the IBA flag, FDIC class OI, or a reviewed override list (below). |
| Banks under 3 years old excluded; band "New bank – not scored" | Start-up losses are expected, but the label counts two losses in a row as distress. |
| CRE concentration stays missing when all three CRE loan types are missing (was 0%) | Missing is not zero. |
| Risk bands from fixed probability cut-offs (was: percentiles of each quarter) | Bands keep the same meaning from quarter to quarter (below). |

Two ways of handling the CBLR ratio were compared on the same split, before the new-bank
exclusion:
- **(a)** drop the ratio: test PR-AUC 0.356;
- **(b)** keep it as missing and add a CBLR flag: PR-AUC 0.357.

They tied because no training year has CBLR banks (the framework began in 2020), so (b)'s flag
never varies in training. (a) was chosen as the simpler option.

### Rows removed from training (2001–2019, rows that pass every other rule)

| Exclusion | Rows | Banks | Labelled distressed |
|---|---|---|---|
| Office of a foreign bank (IBA flag) | 1,076 | 20 | 89 |
| New bank, under 3 years old | 9,342 | 1,763 | 3,172 (34.0%) |

The foreign-office rows were already outside v4's training data: v4 read their 0% Tier 1 as
"already in distress". That exclusion therefore changes nothing in training.

**Override list.** Banks with both capital ratios at 0 in 4 or more quarters, no CBLR flag and no
foreign-office flag go to `gold.capital_zero_review` and a `REVIEW:` line in the gold run log.
They are not excluded automatically. After review they may join the override list
(`FOREIGN_OFFICE_OVERRIDES` in `03_gold.py` and `foreign_office_overrides` in
`dbt/dbt_project.yml`), with the evidence beside each entry. Today the list has one bank:
- **The Bank of East Asia Ltd., NY** (CERT 26028)
  - FDIC class NC with IBA flag 0;
  - both capital ratios 0 in all 53 quarters it reports (2013 Q2 to 2026 Q2);
  - FDIC also lists an OI branch with the same name (CERT 33646).

Banks whose zero or negative capital is real keep their reported ratios and their distress
labels. Examples are insolvent banks in the quarter before failure, such as Rock River Bank in
2009 Q2 and Premier American Bank in 2009 Q4.

### Test results, 2020–2024 (same 86,296 bank-quarters for every model; 4,761 distressed, base rate 5.5%)

| Model | ROC-AUC | PR-AUC | Top 10% capture | Brier |
|---|---|---|---|---|
| v4 | 0.8519 | 0.335 | 53.0% | 0.0436 |
| **v5** | 0.8515 | 0.333 | 52.6% | 0.0436 |
| Logistic regression baseline (v5 data) | 0.733 | 0.207 | 39.0% | 0.0481 |
| Challenger, strict label, judged on the main label | 0.764 | 0.233 | 40.1% | 0.0475 |
| Challenger judged on its own label (base rate 3.1%) | 0.858 | 0.243 | 56.2% | 0.0266 |

v4's figure at registration (test PR-AUC 0.357 on 86,558 rows) was inflated. Those rows still
included banks under 3 years old, whose start-up losses trigger the "two losses" label and are
easy to predict.

**Calibration (test, 10 equal-size groups).** Probabilities run slightly high:

| Group | v5 predicted | v5 observed | v4 predicted | v4 observed |
|---|---|---|---|---|
| Riskiest 10% | 34.5% | 29.0% | 35.0% | 29.3% |
| Safest 10% | 0.49% | 0.36% | 0.53% | 0.25% |

Recalibration is a separate later change; v5 is fixes only.

### Challenger (label sensitivity check; logged in MLflow, not registered)

Stricter label: failure within 12 months, or Tier 1 < 5%, or non-current loans > 3% in the next
4 quarters (no "two net losses" condition). Same features, rows and split. It ranks its own,
rarer outcome well (ROC-AUC 0.858) but has lower PR-AUC because there are fewer positives. Judged
on the main label it is clearly weaker, so the main label stays. It does not become champion
without approval.

### Bands

Each band has a fixed meaning: the share of its banks that became distressed within 4 quarters,
measured out of sample on the training years. Each year from 2003 to 2019 was predicted by a model
trained only on years at least 2 earlier, whose labels were complete. 2001–2002 have no earlier
years to learn from. The 2020–2024 test years were not used to set the cut-offs. Each cut-off is
the lowest one that keeps its band at its target, so bands are as wide as their meaning allows and
sit at the lower edge of their range.

| Band | Meaning | Probability | Rate 2003–2019 (bank-quarters) | Rate 2020–2024, report only |
|---|---|---|---|---|
| High | ≥ 50% became distressed | ≥ 0.358 | 50.0% (35,858) | 43.8% (3,177) |
| Elevated | 25–50% | 0.163 – 0.358 | 25.0% (45,564) | 20.0% (5,899) |
| Watch | 10–25% | 0.036 – 0.163 | 10.0% (123,720) | 6.5% (23,853) |
| Low | < 10% | < 0.036 | 2.8% (188,966) | 1.2% (53,367) |

The cut-offs are stored as tags on the model version (`band_cutoff_high` 0.358027,
`band_cutoff_elevated` 0.162898, `band_cutoff_watch` 0.035561), so each version carries its own
bands. In 2020–2024 every band's observed rate sits below its 2003–2019 rate, because distress was
rarer than in the 2008–2012 crisis. Bands are about relative risk across time, not a forecast of
this year's failure rate.

Band counts (bank-quarters not already in distress; 2003–2019 out of sample, 2020–2024 test):

| Year | High | Elevated | Watch | Low |
|---|---|---|---|---|
| 2003 | 2,256 | 2,814 | 7,482 | 18,455 |
| 2004 | 1,864 | 2,673 | 7,817 | 18,593 |
| 2005 | 1,451 | 2,238 | 7,508 | 19,523 |
| 2006 | 1,354 | 2,168 | 6,980 | 19,164 |
| 2007 | 1,720 | 2,645 | 7,817 | 15,696 |
| 2008 | 2,901 | 3,528 | 8,166 | 9,706 |
| 2009 | 4,015 | 3,556 | 6,897 | 5,480 |
| 2010 | 3,897 | 3,552 | 6,777 | 4,525 |
| 2011 | 3,610 | 3,633 | 7,261 | 4,630 |
| 2012 | 2,995 | 3,517 | 7,854 | 5,442 |
| 2013 | 2,668 | 3,387 | 8,449 | 6,184 |
| 2014 | 1,921 | 2,917 | 8,589 | 7,865 |
| 2015 | 1,377 | 2,370 | 7,916 | 9,634 |
| 2016 | 1,117 | 2,014 | 7,140 | 10,520 |
| 2017 | 1,020 | 1,719 | 6,291 | 10,984 |
| 2018 | 860 | 1,427 | 5,535 | 11,491 |
| 2019 | 832 | 1,406 | 5,241 | 11,074 |
| 2020 | 772 | 1,512 | 5,398 | 10,281 |
| 2021 | 467 | 934 | 4,186 | 12,080 |
| 2022 | 515 | 938 | 4,405 | 11,576 |
| 2023 | 610 | 1,160 | 5,106 | 10,134 |
| 2024 | 813 | 1,355 | 4,758 | 9,296 |

2026 Q2 (one quarter, 4,257 banks): High 150, Elevated 230, Watch 878, Low 2,618, Already in
distress 346, New bank – not scored 20, Office of a foreign bank – not scored 15.

### Release checks

Before a version is promoted, and in every weekly run, the 20 riskiest scored banks must contain
no new bank, no foreign-bank office, no 0% or missing Tier 1 leverage and no reason quoting an
unusable capital ratio (`sanity_check_top20`). The [runbook](runbook.md) has the review steps. v5
passed on 2026-10-25.

### Known limits

- **CBLR banks score less well.** On 2020–2024 the model separates CBLR banks less well than other
  banks (ROC-AUC 0.833 vs 0.866 in the variant comparison) because training has no CBLR rows.
- **Slight over-prediction.** Probabilities run a little high (35% predicted vs 29–30% observed in
  the riskiest 10%). Bands are set from observed rates, so their meaning holds, but read a
  probability as a ranking more than an exact chance.
- **Override list.** Foreign-bank offices that the FDIC does not flag (IBA or class OI) are only
  excluded once a person adds them to the override list. Until then they are scored as normal
  banks and appear in `gold.capital_zero_review`.
- **Immaterial losses count.** The "two losses in a row" trigger fires on any loss, however
  small. Example: Transact Bank, CO ($6.5M of assets, 2026 Q2 score 0.748) breaks even on about
  $10k a quarter, and 2026 Q2 was a $3k loss. The stricter-label challenger scores it 0.098.
- **Securities losses look like operating losses.** Net income includes realised gains and losses
  on securities, so a one-off securities sale reads like weak earnings. Example: Banc of California,
  2026 Q2. Its $233M net loss was a $254.5M realised securities loss plus a $162M provision. The
  provision and the charge-offs are a real credit signal; the securities loss is not.
- **Young and recently merged banks.** Banks just past the 3-year cut-off can still have start-up
  economics, and a merger changes growth, capital and earnings in one quarter. Example: Genesis
  Bank, CA, 4.9 years old, which absorbed EH National Bank in June 2025. Its score is earnings-driven
  (0.072 with earnings at the median) and its credit is clean.
- **Credit risk and earnings risk share one list.** Banks just under the 3% non-current-loan line
  (32 of the top 50 in 2026 Q2) and banks with thin earnings both rank high. Users can't tell
  from the probability which kind of risk a bank carries; the reasons show it.
- Public data only, quarterly, with a lag of about 2 months after quarter-end.
- The label mixes capital, asset quality and earnings stress, so "distress" is not the same as
  failure.

## v6 candidates (not implemented)

Changes to consider for the next version. Each needs its own comparison on the same split before
it is adopted.

| Issue (v5) | Example | Candidate change |
|---|---|---|
| "Two losses in a row" fires on immaterial losses at micro-banks | Transact Bank, CO: $3k loss, score 0.748 | Count a loss only if it is material, e.g. above a share of assets or of equity |
| Realised securities losses look like operating losses | Banc of California, 2026 Q2: $254.5M securities loss | An earnings feature that excludes realised securities gains and losses (FDIC `IGLSEC`), next to the reported figure |
| Young and recently merged banks keep start-up or merger effects | Genesis Bank, CA: 4.9 years old, merged June 2025 | Extend the not-scored rule to 5 years, or add a recent-merger flag from the FDIC structure-change history |
| One list mixes credit risk and earnings risk | 32 of the top 50 have non-current loans of 2–3% | Separate outputs: asset-quality risk and failure risk (fits with the planned hazard model) |
| Probabilities run slightly high | Riskiest 10%: 34.5% predicted, 29.0% observed | Calibrate on out-of-sample predictions from the training years |

## v7 candidate: complaint features as a tested challenger (not implemented)

v5 uses FDIC call-report features only; CFPB complaints are shown as context. v7 would test
whether complaints add predictive value, as a challenger that is adopted only on evidence.

**Data.** Ingest the CFPB complaint history back to its start in late 2011, so 2012–2019
training rows can have complaint features. The current pipeline loads complaints only from
October 2023, so today no training year has them.

**Candidate features** (per bank-quarter, for banks matched to a CFPB company):
- complaints per $1bn of deposits;
- change over 4 quarters;
- share of complaints about account management or fees;
- share with untimely company responses.

**Test of incremental value.**
- Compare a v6-based model with and without the complaint features, using the same split and
  the same test rows.
- Report the results overall and separately for large banks, where complaints are dense enough
  to carry a signal.
- Use the usual measures: ROC-AUC, PR-AUC, riskiest-10% capture and calibration.

**Limits to address.**
- **Large banks dominate.** Complaints cluster at large banks; most small banks have few or none
  in a quarter, so the features are close to empty for them.
- **The database itself changed.** Products were added over time and complaint narratives start
  in 2015, so raw counts grow with the database, not only with bank behaviour. Features must be
  normalised for that growth, e.g. as a bank's share of all matched complaints that quarter.
- **Matching is partial.** Only 30% of complaints are matched to an FDIC bank. Banks without a
  match need a "no match" flag rather than zero complaints.

**Adoption rule.** Adopt only if the features clearly improve the results without hurting
calibration. Otherwise complaints stay as context next to the scores.
