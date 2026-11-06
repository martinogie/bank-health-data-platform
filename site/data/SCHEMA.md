# Website data contract (schemaVersion 1) — DRAFT for Martins' OK

The line between the backend and bank.martinsogiekpolor.com. The site reads only these files; every
number on it comes from them, never from the page source. Each file carries `schemaVersion`,
`generatedAt` (ISO time), `quarter` (`YYYY-MM-DD`, the FDIC quarter) and, for fixtures,
`"fixture": true` (the site then shows a small "preview data" label). JSON Schemas for each file
live next to this document and are validated in CI.

Files (in `site/public/data/`):

| File | Size (approx.) | Used by |
|---|---|---|
| `overview.json` | 15 KB | Overview |
| `banks_index.json` | 450 KB | Watchlist search, Overview filters |
| `banks/<cert>.json` | ~2 KB each, ~4,300 files | Bank page, Watchlist cards |
| `model.json` | 10 KB | Methods |
| `pipeline.json` | 10 KB | Pipeline |

Bands, as codes everywhere: `H` High, `E` Elevated, `W` Watch, `L` Low, `D` Already in distress,
`N` New bank – not scored, `F` Office of a foreign bank – not scored.

## `overview.json`

| Key | Type | Meaning |
|---|---|---|
| `modelVersion` | int | Model version that scored this quarter (5 today) |
| `banksScored` | int | Banks with a probability |
| `bandCounts` | object | Band code → number of banks this quarter |
| `bandMeaning` | object | Band code → `{min, max}` probability and the plain meaning ("≥ 50% became distressed") |
| `bandsByYear` | array | `{year, H, E, W, L}`: bank-quarters per band per year (2003 onward; out of sample up to 2019, test 2020–2024; after 2024 the latest scores) |
| `top20` | array | `{rank, cert, name, state, probability, band, reason, driver}` (the 20 highest probabilities) |

**`driver`** (`credit` \| `earnings`): of the two what-if probabilities in the bank's file (below),
the group whose reset to the median lowers the probability more. Shown as the Credit / Earnings
chip.

## `banks_index.json`

`{schemaVersion, generatedAt, quarter, banks: [...]}`, one compact row per bank (short keys, for
search):

| Key | Meaning |
|---|---|
| `c` | FDIC certificate number |
| `n` | Name |
| `ci`, `st` | City, state |
| `a` | Total assets, $ millions |
| `b` | Band code |
| `p` | Probability (3 decimals; `null` for `N`, `F`) |
| `r` | Top reason, plain words (`null` if none) |

## `banks/<cert>.json`

| Key | Type | Meaning |
|---|---|---|
| `profile` | object | `name, city, state, cert, assetsM, established` (date), `ageYears`, `tier` (1–5) |
| `probability` | number \| null | 3 decimals |
| `band` | string | Band code |
| `rank` | int \| null | Rank among scored banks (1 = highest) |
| `peerPercentile` | int \| null | Share of same-tier banks with a lower probability, 0–100 |
| `reasons` | array (≤ 3) | `{text, feature, value, median, unit}`: SHAP reasons, compared with the median of the same asset tier |
| `whatIf` | object \| null | `{today, earningsAtMedian, creditAtMedian}`: the probability now, and with that group of features set to the tier median (see below). `null` for bands `D`, `N`, `F` |
| `driver` | string \| null | `credit` \| `earnings` (as in overview) |
| `waterfall` | object \| null | The SHAP waterfall, exactly as `w` in today's `waterfalls/<cert>.json` (base, up to 8 items, other) |
| `distressLines` | array \| null | Band `D` only: the distress lines crossed (as `d` today) |
| `history` | object | 8 quarters, oldest first, aligned with `quarters`: `ncl` non-current loans %, `t1` Tier 1 leverage %, `roa` ROA %, `ni` quarterly net income % of assets, `eff` efficiency ratio %. `null` where not reported |
| `quarters` | array | The 8 quarter labels (`2024 Q3` …) |
| `complaints` | object \| null | Matched banks only: `{quarter, per1bDeposits}` |
| `flags` | array | Any of `cblr` (community bank leverage ratio filer), `newBank`, `foreignOffice`, `recentMerger` |

**`whatIf`**, defined: in the scoring notebook, the bank's features of one group are set to the
median of its asset tier and the model scores it again (2 extra predictions per bank). Groups:
**credit** = non-current loans, past-due, net charge-offs and their changes; **earnings** = ROA,
ROE, net interest margin, efficiency and their changes (exact feature lists fixed in the scoring
code and listed on the Methods page).

## `model.json`

`version`, `registered`, `metrics` (test 2020–2024: ROC-AUC, PR-AUC, top-10% capture, Brier, for v5,
the threshold rule and the logistic baseline, with bootstrap intervals), `challenger` (same, strict
label), `bands` (`code, min, max, trainRate, testRate`), `calibration` (10 groups: predicted vs
observed), `monitoring` (latest PSI and backtest from `OPS.MODEL_MONITORING`), `limitations`
(`{title, text}` from the model card). Source: MLflow (version 5 run and tags) and the model card.

## `pipeline.json`

`layers` (`{layer, where, rows, explanation}` from the latest run's `ROWS_DETAIL`), `datasets`
(per source: FDIC financials, institutions, failures, CFPB complaints), `checks` (`{name, text}`:
contracts, freshness, volumes, dbt tests, top-20 release check, promotion gate), `runs` (the last 12
from `OPS.HEALTH_RUNS_LAST_12`, per the [health page contract](../../docs/health_page_contract.md):
date, result, duration, Databricks minutes, credits, banks scored).

## What exists today vs what's new

| Data | Today | New in Stage B |
|---|---|---|
| Probability, band, reasons, waterfall, distress lines, 8-quarter `ncl t1 roa eff`, complaints | `watchlist.json` + `waterfalls/` (schema 5) | reshaped into these files |
| Quarterly net income history (`ni`) | in `MART_BANK_SCORECARD` | added to the export |
| `whatIf`, `driver` | — | **05_score**: 2 extra predictions per bank (credit, earnings at the tier median), stored with the scores |
| Band counts by year, model metrics, calibration | MLflow (v5), model card | exported to `overview.json`, `model.json` |
| Recent runs, layer counts | `OPS.HEALTH_RUNS_LAST_12`, `PIPELINE_RUNS` | exported to `pipeline.json` |
