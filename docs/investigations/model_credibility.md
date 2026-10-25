# Model credibility investigation (step 1)

Findings D-03, M-02 and M-04 from the project review. Investigation only: **no code or model has
changed**. Data: FDIC call reports in Databricks (`gold.bank_quarter`, `ml.model_frame`,
`ml.risk_scores`, model v4, scored 2026 Q2) plus the live FDIC BankFind API for fields we don't
load (`CBLRIND`, `IBA`, `RWAJT`). Queried 2026-10-24. All numbers below come from those queries.

## Summary

1. **D-03: the zeros come from the FDIC, not from our code.** Every bank that uses the Community
   Bank Leverage Ratio (CBLR, flag `CBLRIND = 1`, available since 2020 Q1) has its total risk-based
   capital ratio (`RBCRWAJ`) reported as **0**, because CBLR banks don't report risk-weighted assets
   (`RWAJT`). In 2026 Q2 that is 1,813 of 4,257 scored banks (42.6%); in the 2001–2019 training data it was 1,227 of 581,454 (0.2%).
   The model never saw this pattern in training, and **25 of the current top 50** have a 0 here.
   159 banks show "total risk-based capital = 0.00%" as one of their three reasons.
2. **A second zero-capital group: US offices of foreign banks.** 15 scored institutions report 0
   for *both* Tier 1 leverage and total risk-based capital, because a branch holds no capital of its
   own: 14 carry the FDIC flag `IBA = 1` (Bank of China, State Bank of India, Standard Chartered,
   BBVA, …) and 1 does not (The Bank of East Asia Ltd., New York, CERT 26028: `IBA = 0`, `CBLRIND`
   blank). A Tier 1 ratio of 0 meets the "Tier 1 < 5%" rule, so **all 15 are wrongly banded
   "Already in distress".**
3. **M-02: new banks look distressed by design.** In 2001–2019, banks under 1 year old had a
   **77.78%** distress-label rate (banks 5+ years: 11.51%), and **95.4%** of those labels came
   from the "two losses in a row" rule alone: start-up losses. 11 of the current top 50 are under 3 years old.
4. **M-04: bands are percentiles, not probabilities.** High is always the top 5% of banks that aren't
   already distressed, whatever the probabilities are. In 2026 Q2 the High band starts at a probability
   of 0.3244, Elevated at 0.105, Watch at 0.0276.
5. **Also found:** missing commercial-real-estate loan lines are filled with 0 before the CRE
   ratios are computed (`03_gold.py`, dbt `int_bank_quarter_ratios.sql`), so "not reported" becomes
   "0% CRE". Small: 528 bank-quarters in 2001–2019 (0.09%) had all three components missing and got
   CRE = 0; none in 2026 Q2. Same kind of error, so worth fixing with the rest.

## a) D-03: missing capital stored as 0

### Where missing values are filled

| Place | What is filled | Effect |
|---|---|---|
| FDIC BankFind API (source) | `RBCRWAJ`, `RBC1AAJ` are computed by the FDIC (capital ÷ risk-weighted or average assets); not-reported inputs come out as **0** | CBLR banks and foreign-bank branches get capital ratios of 0 |
| `databricks/notebooks/03_gold.py` lines 61–63 | `coalesce(construction, 0) + coalesce(multifamily, 0) + coalesce(nonfarm_nonres, 0)` | CRE loans = 0 when all three are missing → `cre_to_loans_pct`, `cre_to_equity_pct` = 0 instead of null |
| `dbt/models/intermediate/int_bank_quarter_ratios.sql` lines 71–72 | same CRE coalesce in Snowflake | same, in the marts and Power BI |
| `databricks/notebooks/_features.py` lines 97–100 | a missing ratio counts as "no breach" when deciding distress | label side: unknown ≠ breach (reasonable) |
| `_features.py` line 118 | no failure record → `fails_within_12m = 0` | correct by definition |
| `04_train_model.py` line 134 | logistic regression: median imputation + missing-indicator, fitted on training data only | baseline only; champion is scikit-learn `HistGradientBoostingClassifier`, which takes NaN natively (not LightGBM) |
| `05_score.py` | reasons: each feature reset to the median of all banks that quarter | a 0 that really means "not reported" is treated as a real value and can become a reason |
| `03_gold.py` / dbt complaints | complaint counts 0 for fully covered quarters | intended: no complaints in a covered quarter is a real 0 |

No `fillna` / `coalesce` / default touches any capital ratio in ingestion, silver, gold, features,
training or scoring. The 0s are already in the raw file: `financials_20260630.jsonl.gz` has
`RBCRWAJ = 0` for 1,831 of 4,313 banks as delivered by the API.

### CBLR banks and the zero capital ratio, every quarter

`CBLRIND` ("Community Bank Ratio") is a 0/1 flag in the FDIC definitions
(`https://api.fdic.gov/banks/docs/risview_properties.yaml`); the FDIC's own derived fields use
`CBLRIND === 0` to mean "not a CBLR bank". It is blank before 2020.

| Quarter | Banks | CBLR banks | Total RBC ratio = 0 | …of which CBLR | …not CBLR | CBLR banks with risk-weighted assets 0/blank |
|---|---|---|---|---|---|---|
| 2019-12-31 | 5,244 | 0 | 17 | 0 | 17 | 0 |
| 2020-03-31 | 5,182 | 1,716 | 1,733 | 1,716 | 17 | 1,716 |
| 2020-06-30 | 5,131 | 1,857 | 1,874 | 1,857 | 17 | 1,857 |
| 2020-09-30 | 5,098 | 1,910 | 1,927 | 1,910 | 17 | 1,910 |
| 2020-12-31 | 5,067 | 1,903 | 1,920 | 1,903 | 17 | 1,903 |
| 2021-03-31 | 5,044 | 1,905 | 1,922 | 1,905 | 17 | 1,905 |
| 2021-06-30 | 5,015 | 1,856 | 1,873 | 1,856 | 17 | 1,856 |
| 2021-09-30 | 4,979 | 1,804 | 1,821 | 1,804 | 17 | 1,804 |
| 2021-12-31 | 4,904 | 1,758 | 1,775 | 1,758 | 17 | 1,758 |
| 2022-03-31 | 4,861 | 1,709 | 1,726 | 1,709 | 17 | 1,709 |
| 2022-06-30 | 4,838 | 1,691 | 1,708 | 1,691 | 17 | 1,691 |
| 2022-09-30 | 4,813 | 1,653 | 1,670 | 1,653 | 17 | 1,653 |
| 2022-12-31 | 4,773 | 1,669 | 1,686 | 1,669 | 17 | 1,669 |
| 2023-03-31 | 4,740 | 1,677 | 1,694 | 1,677 | 17 | 1,677 |
| 2023-06-30 | 4,714 | 1,691 | 1,708 | 1,691 | 17 | 1,691 |
| 2023-09-30 | 4,686 | 1,707 | 1,724 | 1,707 | 17 | 1,707 |
| 2023-12-31 | 4,658 | 1,683 | 1,700 | 1,683 | 17 | 1,683 |
| 2024-03-31 | 4,640 | 1,694 | 1,711 | 1,694 | 17 | 1,694 |
| 2024-06-30 | 4,610 | 1,689 | 1,706 | 1,689 | 17 | 1,689 |
| 2024-09-30 | 4,589 | 1,691 | 1,708 | 1,691 | 17 | 1,691 |
| 2024-12-31 | 4,560 | 1,701 | 1,718 | 1,701 | 17 | 1,701 |
| 2025-03-31 | 4,536 | 1,732 | 1,749 | 1,732 | 17 | 1,732 |
| 2025-06-30 | 4,494 | 1,717 | 1,734 | 1,717 | 17 | 1,717 |
| 2025-09-30 | 4,452 | 1,726 | 1,743 | 1,726 | 17 | 1,726 |
| 2025-12-31 | 4,411 | 1,722 | 1,739 | 1,722 | 17 | 1,722 |
| 2026-03-31 | 4,353 | 1,748 | 1,765 | 1,748 | 17 | 1,748 |
| 2026-06-30 | 4,313 | 1,814 | 1,831 | 1,814 | 17 | 1,814 |

Every CBLR bank has a 0 total risk-based capital ratio, in every quarter. The constant 17 non-CBLR
zeros (all with `CBLRIND` blank) are 15 US branches of foreign banks with `IBA = 1`
("International Banking Act entity"), The Bank of East Asia Ltd. New York (`IBA = 0`), and one more
filer. Two of the 17 (CERT 25927 and the `IBA` branch CERT 58339) are not in the institutions list,
so 15 are scored.

### Foreign-bank offices with zero capital ratios, 2026 Q2 (14 with IBA = 1, plus CERT 26028)

| Band given by model v4 | Branches |
|---|---|
| Already in distress | 15 |

Both capital ratios are 0 because a branch holds no capital of its own; the parent bank does.

### Missing vs zero, every model feature

Missing = null. Zero = exactly 0. Latest quarter = 2026 Q2 (4,257 scored bank-quarters);
training = all bank-quarters 2001–2019 in `ml.model_frame` (581,454). Sorted by the share of zeros in 2026 Q2.

| Feature | 2026 Q2 missing | 2026 Q2 zero | 2001–19 missing | 2001–19 zero | Zero real or missing? |
|---|---|---|---|---|---|
| `ore_to_assets_pct` | 15 (0.4%) | 2,841 (66.7%) | 1,216 (0.2%) | 236,728 (40.7%) | real: no foreclosed property |
| `brokered_to_deposits_pct` | 19 (0.4%) | 2,305 (54.1%) | 1,791 (0.3%) | 375,878 (64.6%) | real: no brokered deposits |
| `past_due_90_to_loans_pct` | 37 (0.9%) | 2,245 (52.7%) | 4,763 (0.8%) | 242,216 (41.7%) | real: no loans 90+ days past due |
| `total_risk_based_capital_pct` | 0 (0.0%) | 1,813 (42.6%) | 0 (0.0%) | 1,227 (0.2%) | **not reported, stored as 0**: CBLR banks and foreign-bank branches; a real ratio is never 0 |
| `ag_to_loans_pct` | 52 (1.2%) | 1,239 (29.1%) | 42,553 (7.3%) | 167,458 (28.8%) | real: no farm loans |
| `nonaccrual_to_assets_pct` | 0 (0.0%) | 727 (17.1%) | 0 (0.0%) | 101,039 (17.4%) | real: no non-accrual assets |
| `net_chargeoff_rate_pct` | 0 (0.0%) | 718 (16.9%) | 0 (0.0%) | 74,131 (12.7%) | real: no net charge-offs |
| `noncurrent_loans_pct` | 0 (0.0%) | 527 (12.4%) | 0 (0.0%) | 58,733 (10.1%) | real: no non-current loans |
| `noncurrent_change_1q_pp` | 4 (0.1%) | 434 (10.2%) | 1,222 (0.2%) | 44,131 (7.6%) | real: no change between periods |
| `past_due_30_89_to_loans_pct` | 37 (0.9%) | 394 (9.3%) | 4,763 (0.8%) | 37,469 (6.4%) | real: no loans 30–89 days past due |
| `noncurrent_change_4q_pp` | 8 (0.2%) | 351 (8.2%) | 5,169 (0.9%) | 34,762 (6.0%) | real: no change between periods |
| `construction_to_loans_pct` | 37 (0.9%) | 340 (8.0%) | 5,270 (0.9%) | 61,829 (10.6%) | real: no construction loans |
| `consumer_to_loans_pct` | 52 (1.2%) | 128 (3.0%) | 5,958 (1.0%) | 7,347 (1.3%) | real: no consumer loans |
| `ci_to_loans_pct` | 37 (0.9%) | 125 (2.9%) | 4,763 (0.8%) | 22,619 (3.9%) | real: no business loans |
| `cre_to_equity_pct` | 16 (0.4%) | 83 (1.9%) | 1,406 (0.2%) | 12,876 (2.2%) | real zeros in 2026 Q2; up to 528 of the 2001–19 zeros come from our CRE coalesce |
| `residential_to_loans_pct` | 37 (0.9%) | 80 (1.9%) | 5,270 (0.9%) | 7,097 (1.2%) | real: no residential mortgages |
| `cre_to_loans_pct` | 37 (0.9%) | 51 (1.2%) | 4,763 (0.8%) | 8,831 (1.5%) | real zeros in 2026 Q2; 528 of the 2001–19 zeros come from our CRE coalesce |
| `loans_to_deposits_pct` | 5 (0.1%) | 33 (0.8%) | 604 (0.1%) | 4,217 (0.7%) | real: no loans (includes the foreign branches) |
| `deposit_growth_yoy_pct` | 14 (0.3%) | 20 (0.5%) | 5,696 (1.0%) | 2,152 (0.4%) | real: no change, or zero deposits both years |
| `net_interest_margin_pct` | 0 (0.0%) | 17 (0.4%) | 0 (0.0%) | 1,430 (0.2%) | 2026 Q2: 14 of 17 are `IBA` branches (verified); 2001–19 not checked |
| `efficiency_ratio_pct` | 0 (0.0%) | 17 (0.4%) | 0 (0.0%) | 1,667 (0.3%) | 2026 Q2: 14 of 17 are `IBA` branches (verified); 2001–19 not checked |
| `tier1_leverage_pct` | 0 (0.0%) | 15 (0.4%) | 0 (0.0%) | 1,223 (0.2%) | **not reported, stored as 0**: 14 `IBA = 1` branches + Bank of East Asia NY (verified for 2026 Q2) |
| `roa_pct` | 0 (0.0%) | 15 (0.4%) | 0 (0.0%) | 1,591 (0.3%) | 2026 Q2: 14 of 15 are `IBA` branches (verified); 2001–19 not checked |
| `tier1_change_4q_pp` | 8 (0.2%) | 15 (0.4%) | 5,169 (0.9%) | 1,214 (0.2%) | real: no change between periods |
| `roa_change_4q_pp` | 8 (0.2%) | 15 (0.4%) | 5,169 (0.9%) | 1,232 (0.2%) | real: no change between periods |
| `loan_growth_qoq_pct` | 43 (1.0%) | 1 (0.0%) | 5,913 (1.0%) | 195 (0.0%) | real: no change between periods |
| `equity_to_assets_pct` | 15 (0.4%) | 0 (0.0%) | 1,216 (0.2%) | 2 (0.0%) |  |
| `roe_pct` | 16 (0.4%) | 0 (0.0%) | 1,271 (0.2%) | 390 (0.1%) |  |
| `net_income_qtr_to_assets_pct` | 19 (0.4%) | 0 (0.0%) | 2,146 (0.4%) | 636 (0.1%) |  |
| `loan_growth_yoy_pct` | 45 (1.1%) | 0 (0.0%) | 9,716 (1.7%) | 69 (0.0%) | real: no change between periods |
| `asset_growth_yoy_pct` | 8 (0.2%) | 0 (0.0%) | 5,169 (0.9%) | 54 (0.0%) | real: no change between periods |
| `log_total_assets` | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |  |

Only the capital ratios jump between the periods (0.2% → 42.6% zero for total risk-based capital).
The other common zeros are plausible business facts and are similar in both periods.

## b) M-02: new banks at the top of the list

Establishment date: FDIC institutions field `ESTYMD`, loaded as `silver.fdic_institutions.established_date`
(present for all 4,257 scored banks). Age = years from `ESTYMD` to the quarter-end.

### Current top 50 by probability (model v4, 2026 Q2)

11 are under 3 years old (7 under 1 year); 16 have an efficiency ratio above 200%; 25 have a total risk-based capital ratio of 0; 43 are already in distress. "Rules met now" are the distress conditions in the scoring quarter itself: non-current loans > 3%, Tier 1 < 5%, net loss in Q1 and Q2 2026.

| # | Bank | State | Probability | Band | Age (yrs) | Assets ($m) | Efficiency % | NCL % | Tier 1 % | Total RBC % | Rules met now |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Solutions Plus Bank | AL | 0.986 | Already in distress | 0.6 **(new)** | 43.9 | 255.4 | 0.0 | 56.7 | 0.0 | 2 losses |
| 2 | Generations Community Bank | IN | 0.982 | Already in distress | 0.3 **(new)** | 30.9 | 1,115.3 | 0.0 | 86.24 | 0.0 | 2 losses |
| 3 | Nano Banc | CA | 0.976 | Already in distress | 18.8 | 736.2 | -5.6 | 25.73 | 5.04 | 9.11 | NCL>3% |
| 4 | Houston Bank & Trust | TX | 0.976 | High | 0.1 **(new)** | 38.5 | 9,841.0 | 0.0 | 93.01 | 0.0 | none |
| 5 | Portrait Bank | FL | 0.966 | High | 0.0 **(new)** | 44.7 | 8,077.4 | 0.0 | 99.23 | 0.0 | none |
| 6 | Echelon Bank | FL | 0.963 | High | 0.1 **(new)** | 52.2 | 2,359.4 | 0.0 | 67.36 | 176.57 | none |
| 7 | Summit National Bank | WY | 0.953 | Already in distress | 42.3 | 65.9 | 233.1 | 0.46 | 2.46 | 6.07 | Tier1<5%, 2 losses |
| 8 | Columbia Savings and Loan Association | WI | 0.949 | Already in distress | 102.5 | 20.6 | 176.0 | 19.47 | 5.46 | 8.87 | NCL>3%, 2 losses |
| 9 | Five Rivers Bank | NJ | 0.934 | Already in distress | 1.1 **(new)** | 78.1 | 464.5 | 0.0 | 57.11 | 0.0 | 2 losses |
| 10 | Grand Rivers Community Bank | IL | 0.928 | Already in distress | 124.5 | 15.1 | 169.4 | 19.04 | 8.44 | 11.96 | NCL>3%, 2 losses |
| 11 | Liberty Bank of New Jersey | NJ | 0.925 | Already in distress | 0.4 **(new)** | 107.4 | 211.6 | 0.0 | 50.46 | 0.0 | 2 losses |
| 12 | Talbot State Bank | GA | 0.918 | Already in distress | 118.6 | 51.7 | 123.7 | 2.35 | 7.78 | 17.61 | 2 losses |
| 13 | Eastern National Bank | FL | 0.918 | Already in distress | 56.8 | 65.2 | 270.1 | 0.08 | 8.01 | 15.17 | 2 losses |
| 14 | Altos Bank | CA | 0.910 | Already in distress | 1.6 **(new)** | 68.8 | 253.8 | 0.0 | 28.45 | 0.0 | 2 losses |
| 15 | Midwest Regional Bank | MO | 0.902 | Already in distress | 132.0 | 949.0 | 69.2 | 6.61 | 10.23 | 12.67 | NCL>3% |
| 16 | Legacy Bank | CA | 0.899 | Already in distress | 4.1 | 112.8 | 142.4 | 1.61 | 8.29 | 0.0 | 2 losses |
| 17 | Solon State Bank | IA | 0.896 | Already in distress | 94.3 | 122.5 | 39.6 | 11.69 | 23.0 | 0.0 | NCL>3% |
| 18 | Young Americans Bank | CO | 0.895 | Already in distress | 38.9 | 15.5 | 455.5 | 10.53 | 11.86 | 204.33 | NCL>3%, 2 losses |
| 19 | First City Bank | GA | 0.892 | Already in distress | 81.0 | 23.0 | 293.8 | 0.0 | 194.25 | 0.0 | 2 losses |
| 20 | Bank of Louisiana | LA | 0.891 | Already in distress | 68.0 | 110.3 | 20.1 | 4.57 | 17.77 | 30.64 | NCL>3% |
| 21 | Colonial Savings, F.A. | TX | 0.887 | Already in distress | 64.5 | 501.8 | 192.2 | 0.38 | 46.07 | 175.73 | 2 losses |
| 22 | Miners Exchange Bank | VA | 0.886 | Already in distress | 44.0 | 93.5 | 107.8 | 3.16 | 12.4 | 22.01 | NCL>3% |
| 23 | Outdoor Bank | KS | 0.883 | Already in distress | 129.5 | 648.8 | 80.7 | 3.83 | 9.24 | 0.0 | NCL>3% |
| 24 | First Security Bank and Trust Company | OK | 0.878 | Already in distress | 75.2 | 226.0 | 74.6 | 13.2 | 10.09 | 0.0 | NCL>3% |
| 25 | Fortuna Bank | OH | 0.877 | Already in distress | 1.6 **(new)** | 57.3 | 225.1 | 0.0 | 34.14 | 0.0 | 2 losses |
| 26 | American Eagle Bank | IL | 0.875 | High | 23.7 | 484.7 | 110.8 | 0.04 | 8.53 | 9.75 | none |
| 27 | Champion Bank | CO | 0.873 | Already in distress | 24.0 | 41.7 | 140.6 | 1.01 | 39.33 | 95.26 | 2 losses |
| 28 | Cornerstone Community Bank | KY | 0.872 | High | 0.1 **(new)** | 44.5 | 171.4 | 0.0 | 277.16 | 220.54 | none |
| 29 | SouthPoint Bank | AL | 0.872 | Already in distress | 20.7 | 1,360.9 | 65.3 | 3.54 | 6.86 | 10.18 | NCL>3%, 2 losses |
| 30 | BCB Community Bank | NJ | 0.869 | High | 25.7 | 3,117.3 | 64.7 | 2.85 | 10.38 | 0.0 | none |
| 31 | First Southern Bank | AL | 0.860 | Already in distress | 92.5 | 679.2 | 67.8 | 10.86 | 8.95 | 0.0 | NCL>3%, 2 losses |
| 32 | State Bank of Nauvoo | IL | 0.856 | Already in distress | 133.4 | 37.7 | 90.0 | 3.71 | 8.31 | 16.68 | NCL>3% |
| 33 | M C Bank & Trust Company | LA | 0.856 | Already in distress | 71.4 | 710.0 | 309.3 | 5.84 | 42.96 | 0.0 | NCL>3% |
| 34 | Heritage Bank National Association | MN | 0.855 | Already in distress | 61.7 | 361.1 | 106.2 | 3.84 | 10.81 | 16.59 | NCL>3% |
| 35 | Monet Bank | TX | 0.854 | Already in distress | 38.3 | 3,160.4 | 20.6 | 25.76 | 31.69 | 136.07 | NCL>3% |
| 36 | Battle Bank, National Association | MN | 0.852 | Already in distress | 112.1 | 180.8 | 393.7 | 0.0 | 33.32 | 0.0 | 2 losses |
| 37 | The First National Bank of Williamson | WV | 0.852 | Already in distress | 123.1 | 120.3 | 112.3 | 2.78 | 20.13 | 0.0 | 2 losses |
| 38 | The Iuka State Bank | IL | 0.852 | Already in distress | 115.6 | 133.0 | 74.4 | 7.98 | 10.25 | 0.0 | NCL>3% |
| 39 | Bank of Vici | OK | 0.851 | Already in distress | 124.5 | 28.9 | 118.5 | 12.31 | 10.23 | 0.0 | NCL>3%, 2 losses |
| 40 | Central Bank | AR | 0.851 | Already in distress | 113.9 | 954.6 | 34.5 | 4.76 | 9.37 | 11.57 | NCL>3% |
| 41 | Twin Cedars Bank | IA | 0.849 | Already in distress | 95.3 | 118.3 | 108.2 | 4.61 | 9.19 | 14.21 | NCL>3%, 2 losses |
| 42 | Icon Business Bank | CA | 0.847 | Already in distress | 2.9 **(new)** | 155.5 | 118.4 | 0.22 | 11.94 | 0.0 | 2 losses |
| 43 | Home Savings Bank, FSB | KY | 0.846 | Already in distress | 85.5 | 25.7 | 147.6 | 7.46 | 10.19 | 0.0 | NCL>3%, 2 losses |
| 44 | Tioga-Franklin Savings Bank | PA | 0.845 | Already in distress | 153.2 | 68.4 | 188.7 | 9.23 | 2.06 | 4.14 | NCL>3%, Tier1<5%, 2 losses |
| 45 | Citizens Community Bank | IL | 0.843 | Already in distress | 33.5 | 531.0 | 70.0 | 5.81 | 9.83 | 0.0 | NCL>3% |
| 46 | Citizens Bank & Trust Company | LA | 0.839 | Already in distress | 80.5 | 183.7 | 73.5 | 7.15 | 11.14 | 0.0 | NCL>3% |
| 47 | Lineage Bank | TN | 0.839 | Already in distress | 99.4 | 197.7 | 260.3 | 1.18 | 15.18 | 21.17 | 2 losses |
| 48 | Milford Building and Loan Association | IL | 0.839 | High | 143.5 | 28.1 | 80.6 | 2.55 | 12.29 | 0.0 | none |
| 49 | First State Bank of San Diego | TX | 0.837 | Already in distress | 71.9 | 68.2 | 114.7 | 2.65 | 9.02 | 28.95 | 2 losses |
| 50 | First Credit Bank | CA | 0.837 | Already in distress | 43.4 | 515.8 | 24.2 | 3.33 | 47.61 | 49.05 | NCL>3% |

### Training data 2001–2019: distress-label rate by bank age

Model rows only (banks not already in distress, outcome observed). "Share of positives" = of the
bank-quarters labelled distressed, the share where each condition occurred in the next 4 quarters
(several can apply, so rows add to more than 100%).

| Age | Bank-quarters | Labelled distressed | Label rate | Two losses | NCL > 3% | Tier 1 < 5% | Failure | Two losses **only** |
|---|---|---|---|---|---|---|---|---|
| <1 yrs | 2,619 | 2,037 | **77.78%** | 98.7% | 4.6% | 0.0% | 0.0% | 95.4% |
| 1-2 yrs | 2,353 | 427 | **18.15%** | 75.2% | 44.5% | 0.7% | 0.2% | 55.5% |
| 2-3 yrs | 4,394 | 713 | **16.23%** | 66.3% | 56.0% | 0.7% | 0.8% | 43.1% |
| 3-5 yrs | 11,138 | 1,725 | **15.49%** | 59.2% | 66.5% | 2.8% | 0.3% | 32.6% |
| 5+ yrs | 446,246 | 51,346 | **11.51%** | 28.6% | 82.7% | 1.3% | 0.2% | 16.8% |

All bank-quarters 2001–2019 by age, including those excluded because the bank was already distressed:

| Age | All bank-quarters | Used for training | Already distressed (excluded) |
|---|---|---|---|
| <1 yrs | 5,127 | 2,619 | 2,470 |
| 1-2 yrs | 5,942 | 2,353 | 3,536 |
| 2-3 yrs | 6,822 | 4,394 | 2,316 |
| 3-5 yrs | 14,962 | 11,138 | 3,461 |
| 5+ yrs | 548,601 | 446,246 | 86,465 |

For young banks the label is driven by the "two losses in a row" rule: losses that are normal for a
bank still building its business. The model learns "young + high costs + losses = distress", which
is why de novo banks fill the top of the list. For mature banks, non-current loans drive the label.

## c) M-04: how the risk bands are set

`databricks/notebooks/05_score.py` ranks banks that are **not** already in distress by probability
and cuts at fixed **percentiles** of that quarter: High = top 5% (percentile > 0.95), Elevated =
next 10% (> 0.85), Watch = next 25% (> 0.60), Low = the rest. Banks meeting a distress rule now are
"Already in distress" and not ranked. So the High band always holds about 5% of banks, in a calm
year or a crisis, and the probability at each edge moves every quarter.

| Band (2026 Q2) | Banks | Lowest probability | Highest probability | Lowest percentile |
|---|---|---|---|---|
| High | 195 | 0.3244 | 0.9755 | 0.9501 |
| Elevated | 388 | 0.105 | 0.3207 | 0.8502 |
| Watch | 971 | 0.0276 | 0.1049 | 0.6002 |
| Low | 2,330 | 0.002 | 0.0276 | 3.0E-4 |
| Already in distress | 373 | 0.0511 | 0.9857 | – |

Implied cut-offs in 2026 Q2: High ≥ 0.3244, Elevated ≥ 0.105, Watch ≥ 0.0276. "Already in distress" spans almost the whole range (0.0511–0.9857).

## Points to decide before step 2

1. **Foreign-bank offices.** Not in the brief. `IBA = 1` alone misses one (CERT 26028), so proposal:
   treat a bank as a foreign-bank office when `IBA = 1` **or** both capital ratios are 0 with `CBLRIND`
   blank. Their capital ratios become not reported, they never trigger the Tier 1 rule, and they get a
   band such as "Branch of a foreign bank – not scored" (their capital is the parent's). Needs `IBA`
   and `CBLRIND` added to the FDIC download.
2. **`CBLRIND` is blank before 2020** (the framework didn't exist). Proposal: `cblr_reporter = 1` only
   when `CBLRIND = 1`, else 0; the capital ratio becomes null for CBLR banks, so training (2001–2019)
   barely changes and the model sees missing capital as missing at scoring time.
3. **The model is scikit-learn `HistGradientBoostingClassifier`, not LightGBM.** It handles NaN
   natively too. Proposal: keep it, so v5 differs from v4 only by the fixes.
4. **CRE coalesce.** Proposal: fix it in the same branch (null when all three components are null).
5. **Band thresholds "chosen on training-period data only".** Proposal: fit on 2001–2016, take
   probabilities for 2017–2019 (the existing validation years, inside the 2001–2019 training period)
   and set fixed cut-offs from them, e.g. High = probability where precision on 2017–2019 is ≥ 50%;
   or match the historical distress rate. Tell me which rule you prefer.

A screening tool on public data, not a supervisory rating.
