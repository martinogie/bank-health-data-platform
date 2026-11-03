# E-01: how well are CFPB complaints matched to FDIC banks?

> **Labels proposed by Claude Code and reviewed by Martins Ogiekpolor (3 Nov 2026):** 248 of 250
> confirmed, 2 marked unsure (`reviewed_by` and `confirmed_by_martins` in the label file). Every
> figure below is computed from these reviewed labels.

Data: prod silver, 3 Nov 2026. Complaints in the pipeline: 1,999,552 (the last 3 years, credit
bureaus excluded), from 5,400 companies. Today 142 companies (600,661 complaints, **30.0%**) are
matched to an FDIC bank.

Files: labels [e01_match_labels.csv](e01_match_labels.csv) (250 companies; how they were reviewed:
[e01_review_instructions.md](e01_review_instructions.md)), metrics
[e01_metrics.json](e01_metrics.json) (`scripts/e01_metrics.py`), before/after
[e01_whatif.json](e01_whatif.json) (`databricks/notebooks/tools/e01_match_whatif`, read-only).

## 1. Precision and recall today

The 2 rows Martins marked unsure (ALCAR INC; CLGF Holdco 1, LLC: credit-card complaints,
identity not confirmed) are left out. TRANSWORLD SYSTEMS INC, in both the
random sample and the top 50, is counted once.

| Measure | Value | 95% interval | Based on |
|---|---|---|---|
| **Precision by company** (matches that are right) | **92.9%** | 86.1%–96.5% (Wilson) | 92 of 99 random matched companies (of 142) |
| **Precision by complaint volume** | **99.9%** | 99.8%–100.0% (bootstrap over companies) | 516,835 of 517,162 complaints; the 7 wrong matches are small (327 complaints) |
| **Recall by complaint volume** (bank complaints that get matched) | **94.8%** | 92.0%–94.8% (range) | missed: 33,170 complaints in the top 50 (a census); 0 in a random 99 of the other 5,208 unmatched companies |
| **Recall by company** | 98.5% | 38.4%–98.6% (range) | not informative: a zero rate in 99 random companies still allows up to 3.7% of 5,208 companies |

How recall's range is set: the upper end assumes no missed bank outside the top 50 (none found in
the random sample); the lower end assumes the other unmatched companies miss at the 95% upper
bound of that zero rate, and takes precision at its lower bound.

**What the 7 wrong matches have in common:** 6 are holding-company matches to small banks
(under $10B) whose complaints look like a non-bank's (debt collection, credit reporting, payday
loans) from states the bank isn't in. The 7th, Independent Bank Corp. (MA), matched a Texas
bank's **name** before its own holding-company name could match (Rockland Trust).

## 2. The unmatched volume

The **50 largest unmatched companies hold 64.2%** of the unmatched complaints (898,154 of
1,398,891). The rest is scaled from the random sample.

| Type | Complaints (estimate) | Share of unmatched |
|---|---|---|
| Debt collectors | 554,206 | 39.6% |
| Other non-banks (mortgage, auto and student lenders, credit reporting and data firms, title) | 535,270 | 38.3% |
| Fintechs | 234,367 | 16.8% |
| Unsure (CLGF Holdco 1, LLC) | 6,787 | 0.5% |
| Credit unions | 35,091 | 2.5% |
| **Missed banks** | **33,170** | **2.4%** |

**Banks or bank owners among the top 50** (checked in FDIC data on 3 Nov):

| Company | Complaints | FDIC bank | Proposed |
|---|---|---|---|
| Bread Financial Holdings | 25,276 | Comenity Capital Bank, cert 57570 (no holding company in FDIC data) | **missed bank** |
| USAA | 7,894 | USAA Federal Savings Bank, cert 32188 ($106B; FDIC HC "…AUTOMOBILE ASSN") | **missed bank** |
| Block (Cash App) | 66,148 | Square Financial Services, cert 59177 | fintech: complaints about Cash App |
| Nelnet | 18,780 | Nelnet Bank, cert 59205 | non-bank: federal loan servicing |
| Toyota Motor Credit | 5,211 | Toyota Financial Savings Bank, cert 57542 | non-bank: auto loans |

## 3. Proposed overrides (not applied; for Martins' approval)

| Company | Today | Proposed | Complaints |
|---|---|---|---|
| Bread Financial Holdings, Inc. | unmatched | **Comenity Capital Bank (57570)** | 25,276 |
| UNITED SERVICES AUTOMOBILE ASSOCIATION | unmatched | **USAA Federal Savings Bank (32188)** | 7,894 |
| INDEPENDENT BANK CORP. | Independent Bank, TX (3076) | **Rockland Trust Company, MA (9712)** | 56 |
| Eagle Financial Services, Inc. | Bank of Clarke, VA | unmatched | 121 |
| Cornerstone Holdings, Inc. | Cornerstone Bank, ND | unmatched | 85 |
| FIRST AMERICAN FINANCIAL CORPORATION | First American Trust, FSB | unmatched | 49 |
| Town Financial Corporation | Citizens State Bank of New Castle, IN | unmatched | 7 |
| Woodlands Financial Services Inc | Woodlands Bank, PA | unmatched | 6 |
| Covington Capital Corporation | Covington County Bank, MS | unmatched | 3 |

**Before → after:** 142 → 138 companies matched; 600,661 → 633,560 complaints matched; match
rate **30.04% → 31.69%**. Nothing else changes (checked against all 5,400 companies).

## 4. Proposed systemic fixes (not applied)

**(a) Expand more abbreviations in name normalisation**, on both the CFPB and the FDIC side.
Today it expands FINL, SVCS, BCORP and BANCORPORATION. Proposed additions: ASSN, NATL, BKG, BK,
BCSHS, BANCSHS, SVGS, SVC, GRP, INTL, AMER, CMNTY, FED, MUT, HLDG(S), MGMT.

- Before → after: 142 → 143 companies, 600,661 → 608,555 complaints, **30.04% → 30.43%**.
- The only change in all 5,400 companies: **USAA now matches USAA Federal Savings Bank by its
  holding company**, so its manual override would not be needed. No other match is gained,
  lost or moved: low risk, and it catches future USAA-style misses.

**(b) Sense check on holding-company matches to small banks.** Rule tested: a holding-company
match to a bank under $10B, where under 25% of the complaints come from the bank's home state
and at least half are about non-bank products, goes to **review** (unmatched until approved)
instead of being matched automatically. 12 of the 88 holding-company matches are to banks under
$10B.

| Variant (non-bank products) | Flagged | Known wrong caught | Known correct flagged | Not in the sample |
|---|---|---|---|---|
| strict: debt collection, payday/personal loans | 7 | 4 of 6 (misses Woodlands, First American) | 0 | ALCAR (unsure), Oak Tree Financial (6), First State Financial (1) |
| **broad: + credit reporting, debt or credit management** | **9** | **5 of 6** (misses First American: its complaints are mortgages) | **0** | ALCAR (unsure), Peoples Financial Corp. (16), Oak Tree Financial (6), First State Financial (1) |

Recommendation: the **broad** variant. Limitation: FDIC data gives one home state per bank;
branch states (FDIC locations) would be better for banks that operate in several states.

**(c) Found on the way: name vs holding-company conflicts.** Only 2 companies match one bank by
name and another by holding-company name: INDEPENDENT BANK CORP. (wrong today; fixed by the
override) and FIRSTBANK (right today: FirstBank, CO). Sending such conflicts to review would have
caught the Independent Bank case; with 2 cases in total, it costs little.

## 5. If all the proposals were applied

Projected on the same labels (not a new measurement): the 7 wrong sampled matches are removed or
corrected, so precision in the sample becomes 99/99; the two missed banks in the top 50 are
matched, so recall by volume rises from 94.8% to about 100% of the known misses, still with
the same uncertainty about the companies outside the top 50. The match-rate alert (PR #29) will
show the new level (about 31.7%) as a step up on the run that applies them.
