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

Note: the review (finding M-03) suggested a stricter variant of "failure or Tier 1 < 5% only". The
challenger above is not that variant: it keeps the non-current-loans trigger and drops only the
two-losses rule. It is reported as is; no new variant was trained.

### How much v5 beats a simple rule (finding M-03)

A rule anyone could apply without a model: rank banks by how close they are to the nearest
distress line (non-current loans 3%, Tier 1 leverage 5%), measured as a share of the threshold.
That measure was chosen over a second one (distance in units of each ratio's spread) on 2001–2019
only (PR-AUC 0.302 vs 0.255), then judged once on the 2020–2024 test rows. Intervals are 95%
bootstrap intervals that resample banks (4,934 banks, 1,000 samples), because one bank's quarters
are not independent. Source: `databricks/notebooks/06_model_report.py`, run 29 Oct 2026.

| 2020–2024 test (86,296 rows, base rate 5.5%) | v5 | Threshold rule | v5 minus rule |
|---|---|---|---|
| PR-AUC | 0.333 (0.313–0.356) | 0.153 (0.139–0.169) | **+0.180** (+0.161 to +0.199) |
| Top-10% capture | 52.6% (50.6–54.6%) | 31.9% (29.9–34.0%) | **+20.6 points** (+18.5 to +22.9) |
| ROC-AUC | 0.852 (0.842–0.861) | 0.637 (0.619–0.655) | +0.214 (+0.197 to +0.232) |

**In short:** v5 roughly doubles the rule's PR-AUC (0.333 vs 0.153) and puts 21 points more of the
distress cases in its riskiest 10% (52.6% vs 31.9%). v5 was better in all 1,000 bootstrap samples.
Being near a threshold matters, but v5 adds a large amount beyond it.

**The strict-label challenger** (figures from the v5 training run, not refitted): judged on the
main label, PR-AUC 0.233 and capture 40.1%; judged on its own rarer label (base rate 3.1%),
PR-AUC 0.243 and capture 56.2%. Dropping the two-losses rule gives a cleaner but rarer outcome
and misses earnings-driven distress, so the main label stays primary.

### Results by segment (finding M-07)

Same test rows, v5 only. **Lift** = PR-AUC divided by the segment's base rate; segments are
compared on lift, because raw PR-AUC rises with the base rate. **Capture** = share of the
segment's distress cases in its own riskiest 10%; the riskiest 10% can hold at most
ceil(10% of rows) cases, so where the base rate is above 10% capture can't reach 100%, and it is
also shown as a share of that maximum. A segment is **too small to judge** with fewer than 30
distressed cases, and **too thin to judge** when they come from fewer than 10 distinct banks.

| Segment | Rows | Distressed (banks) | Base rate | ROC-AUC | PR-AUC | Lift | Capture | Capture / max |
|---|---|---|---|---|---|---|---|---|
| < $100M | 12,987 | 1,643 (393) | 12.7% | 0.808 | 0.431 | 3.4 | 38.0% | 48.1% of 79.1% |
| $100M–$1B | 55,051 | 2,611 (667) | 4.7% | 0.844 | 0.299 | 6.3 | 54.0% | 54.0% |
| $1B–$10B | 15,379 | 415 (119) | 2.7% | 0.846 | 0.226 | 8.4 | 54.9% | 54.9% |
| $10B–$100B | 2,251 | 50 (19) | 2.2% | 0.873 | 0.249 | 11.2 | 64.0% | 64.0% |
| > $100B | 628 | 42 (13) | 6.7% | 0.666 | 0.240 | 3.6 | 33.3% | 33.3% |
| Midwest | 38,669 | 2,040 (479) | 5.3% | 0.852 | 0.320 | 6.1 | 52.9% | 52.9% |
| Northeast | 9,119 | 525 (127) | 5.8% | 0.830 | 0.366 | 6.4 | 50.7% | 50.7% |
| South | 30,387 | 1,679 (410) | 5.5% | 0.866 | 0.345 | 6.2 | 54.3% | 54.3% |
| West | 8,025 | 501 (124) | 6.2% | 0.829 | 0.322 | 5.2 | 48.9% | 48.9% |
| Territories / unknown | 96 | 16 (5) | 16.7% | – | – | – | – | too small to judge |
| CBLR reporters | 31,355 | 1,998 (526) | 6.4% | 0.830 | 0.310 | 4.9 | 47.8% | 47.8% |
| Not CBLR | 54,941 | 2,763 (723) | 5.0% | 0.864 | 0.350 | 7.0 | 56.4% | 56.4% |

95% bank-bootstrap intervals for each segment are in the notebook output; the widest are for
> $100B (ROC-AUC 0.50–0.85, capture 17–59%) and $10B–$100B (capture 44–83%).

- **Regions** perform about the same (lift 5.2–6.4).
- **Banks under $100M** have the highest distress rate. The model separates them reasonably
  (ROC-AUC 0.81) but with the lowest lift (3.4), and it fills 48% of the possible top-10% slots.
- **$1B–$100B** banks show the highest lift (8.4 and 11.2), on fewer cases.
- **Banks over $100B** are where v5 is weakest: lift 3.6, ROC-AUC 0.666 with an interval reaching
  0.50 (no better than chance). Their 42 distressed quarters come from **13 distinct banks**, so the
  tier passes the "10 distinct banks" rule and is **not** flagged as too thin, but the evidence is
  narrow. What made those cases distressed:

  | > $100B: trigger in the next 4 quarters | Cases (of 42) | Banks |
  |---|---|---|
  | Two losses in a row | 26 (22 with no other trigger) | 9: USAA FSB, BMO, KeyBank, TD Bank, Flagstar, Capital One NA, Discover, MUFG Union, Capital One Bank (USA) |
  | Failure within 12 months | 12 | 3: Silicon Valley Bank, Signature Bank, First Republic Bank (2023) |
  | Non-current loans > 3% | 8 | M&T (4), Flagstar (3), USAA FSB (1) |
  | Tier 1 < 5% | 0 | – |

  So over half of the largest tier's cases (22 of 42) are the two-losses rule alone. Whether those
  losses were one-off events, such as realised securities losses, is not checked here. The v6
  items 6.1 (count only material losses) and 6.2 (separate securities gains and losses from
  operating earnings) are aimed at exactly this ([roadmap](ROADMAP.md)). The other large group is
  the three 2023 failures, which were driven by deposit runs and unrealised securities losses,
  risks the call-report ratios used here don't capture well.
- **CBLR reporters** score less well than other banks (ROC-AUC 0.830 vs 0.864, lift 4.9 vs 7.0,
  capture 47.8% vs 56.4%), confirming the known gap: training (2001–2019) has no CBLR rows.

**For watchlist users:** v5 is not reliable for the very largest banks (over $100B); judge those on
their own filings. A small CBLR bank's rank is somewhat less reliable than other banks'.

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

### Outliers kept, not removed (finding D-05)

Two dbt tests on `fct_bank_quarter` only **warn**:
- `cre_to_loans_pct` above 100%: 132 bank-quarters;
- `tier1_leverage_pct` outside −50 … 100: 672 bank-quarters.

Both counts are from the dbt run of 25 Oct 2026, and the same in the dev build of 26 Oct 2026.
These rows stay in the data and in the model's population. Why:

- **They are what the banks reported.** The values come from FDIC call reports. An unusual
  value is evidence about that bank; removing it would make the data look cleaner than the
  banks are.
- **Impossible is different from unusual.** Where a value is *not reported* rather than
  extreme, the pipeline already treats it as missing, not as a number. For example, capital
  ratios of banks that don't report them are NULL, not 0 (finding D-03, test
  `assert_unreported_capital_is_null`).
- **The model is robust to extremes.** Gradient-boosted trees split on thresholds, so an
  extreme value falls into the outermost branch instead of stretching a fitted coefficient.
  Brand-new banks, the usual source of extreme early ratios, are not scored at all (younger
  than 3 years).

Warnings must not scroll past unseen. Every run, the failing rows are stored with their keys
(`cert`, `report_date`), and up to 20 sample keys plus the count go to
`OPS.DBT_WARNINGS_AUDIT`. The health page shows the count per run (`WARNED_ROWS`). To look
at them:
`SELECT * FROM BANK_HEALTH.OPS.DBT_WARNINGS_AUDIT ORDER BY RECORDED_AT DESC;`

### Known limits

- **CBLR banks score less well.** On 2020–2024 the model separates CBLR banks less well than other
  banks (ROC-AUC 0.830 vs 0.864, top-10% capture 47.8% vs 56.4%; see Results by segment) because
  training has no CBLR rows.
- **The largest banks.** For banks over $100B, v5 is weak (ROC-AUC 0.666, interval reaching 0.50),
  and most of their distress cases come from the two-losses rule or the 2023 failures. Not reliable
  for those banks.
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

## v6 candidates (planned, not implemented)

**Goal:** a sharper definition of distress and a time-to-distress model. v6 is one release,
compared with v5 on the same 2020–2024 test rows. The order of work is in
[ROADMAP.md](ROADMAP.md).

| # | Item | Evidence from the v5 review | Proposed approach |
|---|---|---|---|
| 6.1 | Count losses toward distress only when they are material | Transact Bank, CO ($6.5M of assets) ranks 7th because a ~$3k quarterly loss can trigger the two-losses rule; the stricter-label challenger ranks it 463 | Require consecutive losses to total at least a threshold share of assets or equity, chosen on 2001–2019 only; report the effect on labels and ranks |
| 6.2 | Separate realised securities gains and losses from operating earnings | Banc of California lost $233M in 2026 Q2, mostly a $254.5M realised securities loss, which the model reads as an operating loss | Ingest the call-report securities gains/losses field; earnings features that exclude it; a separate securities-loss feature |
| 6.3 | Handle young and recently merged banks | Genesis Bank, CA (4.9 years old; absorbed EH National Bank in June 2025) scores 0.719 on earnings alone; the challenger ranks it 549 | Test a 5-year new-bank rule against a recent-merger flag from the FDIC merger history; pick on training data only |
| 6.4 | Report credit risk and failure risk as separate outputs | The v5 top 20 mixes credit warnings (NCL near 3%) with earnings warnings; 32 of the top 50 have NCL of 2–3% | An asset-quality warning score and a failure-risk score (from 6.6), both on the watchlist and bank pages |
| 6.5 | Calibrate the classifier's probabilities | In the riskiest 10%, v5 predicts 34.5% against 29.0% observed | Isotonic or Platt calibration fitted on time-ordered out-of-sample predictions from 2001–2019 only |
| 6.6 | Time-to-distress hazard model | v5 gives one 4-quarter probability; it can't say when distress is likely or rank by failure risk on its own | Discrete-time hazard model (below) |

**6.6 hazard model in more detail.**
- Discrete-time hazard on one row per bank per quarter at risk.
- Outcome on the v6 distress definition, plus a failure-only variant that feeds 6.4.
- Censoring for mergers and for the latest quarters, whose outcome isn't known yet.
- Terms for bank-age bands and for calendar or macro conditions.
- Risk curves at 1, 2, 4 and 8 quarters, and hazard ratios.
- A sensitivity run that treats "merged while weak" as an event.
- Checked with time-dependent AUC and Brier score at each horizon, and compared with the
  classifier at 4 quarters on the same test rows.
- It depends on 6.1 and 6.3, because the distress definition must be settled first.

## v7 candidate: complaint features as a tested challenger (planned, not implemented)

v5 uses FDIC call-report features only; CFPB complaints are shown as context. v7 would test
whether complaints add predictive value, as a challenger that is adopted only on evidence. It
starts after v6 is released; the order of work is in [ROADMAP.md](ROADMAP.md).

**Data.** Ingest the CFPB complaint history back to its start in late 2011, with the same entity
resolution as today, so 2012–2019 training rows can have complaint features. The current pipeline
loads complaints only from October 2023, so today no training year has them.

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
calibration, and record the adopt or reject decision. Otherwise complaints stay as context next to
the scores.
