# Website data contract (schemaVersion 1)

The line between the backend and the Bank Health site (`finance.martinsogiekpolor.com/bankhealth`).
The site reads only these files at runtime; every number on it comes from them, never from the page
source. JSON Schemas: [common.schema.json](../../ingestion/site_contract/schemas/common.schema.json) and
[files.schema.json](../../ingestion/site_contract/schemas/files.schema.json), checked by `scripts/validate-data.mjs` in CI.
Agreed with Martins on 6 Nov 2026 (decision 2: one file per bank, no past-due series).

**Every file** carries:

| Key | Meaning |
|---|---|
| `schemaVersion` | `1` |
| `generatedAt` | ISO time the file was written |
| `quarter` | The FDIC quarter, `YYYY-MM-DD` |
| `dataAsOf` | `{quarterLabel: "2026 Q2", updated: "YYYY-MM-DD"}`: shown in the header strip as "Data as of 2026 Q2 · updated <run date>" |
| `fixture` | `true` for preview data (the site then shows a "Preview data" label); absent or `false` for live data |

Files (in `site/public/data/`, later served from R2; see [PLAN.md](../PLAN.md)):

| File | Size (preview data) | Used by |
|---|---|---|
| `overview.json` | 6 KB | Overview |
| `banks_index.json` | 750 KB (130 KB compressed) | Watchlist search, Bank finder |
| `banks/<cert>.json` | ~3 KB each; 26 in the preview, ~4,300 live | Bank page, Watchlist cards |
| `model.json` | 5 KB | Model & methods |
| `pipeline.json` | 2 KB | Pipeline |

Bands, as codes everywhere: `H` High, `E` Elevated, `W` Watch, `L` Low, `D` Already in distress,
`N` New bank – not scored, `F` Office of a foreign bank – not scored.

## Drivers and pushes (decision 1b)

Taken from the bank's existing SHAP waterfall; **no re-scoring**. Each waterfall item's push
(percentage points) is added to one of two groups:

- **credit** (loan quality): non-current loans, past-due loans, net charge-offs, loan mix and
  concentration, and their changes;
- **earnings**: ROA, ROE, net interest margin, efficiency, quarterly net income to assets, and their
  changes.

`pushes = {credit, earnings}` is the sum of the **positive** pushes in each group. `driver` is the
group with the larger push, shown as the Credit / Earnings chip. On the page they are labelled
"Push from loan quality" and "Push from earnings", with a note that they come from the model's
explanation, not a re-score. The re-score version (each group set to the tier median and scored
again) is on the v6 list in the model card.

## `overview.json`

| Key | Meaning |
|---|---|
| `modelVersion` | Model version that scored this quarter |
| `banksScored` | Banks with a probability |
| `bandCounts` | Band code → number of banks this quarter |
| `bandMeaning` | Band code → `{min, max, meaning}`: probability cut-offs and the plain meaning |
| `keyFigures` | `{bankQuarters, failures}`: call-report rows and FDIC failure/assistance records |
| `bandsByYear` | `{year, H, E, W, L}`: bank-quarters per band per year from 2003 (out of sample to 2019, test 2020–2024, then the latest scores) |
| `top20` | `{rank, cert, name, state, probability, band, reason, driver}` |

## `banks_index.json`

`preview` (fixtures only): the certs that have a full bank file. `banks`: one compact row per bank.

| Key | Meaning |
|---|---|
| `c` | FDIC certificate number |
| `n` | Name |
| `ci`, `st` | City, state |
| `a` | Total assets, $ millions |
| `b` | Band code |
| `p` | Probability (3 decimals; `null` for `N`, `F`) |
| `r` | Top reason in plain words (`null` if none) |

## `banks/<cert>.json`

| Key | Meaning |
|---|---|
| `profile` | `name, city, state, cert, assetsM, ageYears, tier` (asset tier 1–5) |
| `probability` | 3 decimals, or `null` |
| `band` | Band code |
| `rank`, `scoredBanks` | Rank among scored banks (1 = highest) and how many were scored |
| `peerPercentile` | Share of same-tier banks with a lower probability, 0–100 |
| `reasons` | Up to 3 `{text}`: SHAP reasons in plain words, compared with the median of similar-size banks; dollar amounts use the site's one rounding rule (below) |
| `pushes` | `{credit, earnings}` in percentage points, or `null` (bands `D`, `N`, `F`) |
| `driver` | `credit` \| `earnings` \| `null` |
| `waterfall` | `{b, i, o}`: base probability, up to 8 items `{f, l, v, m, pp}` (feature, label, value, tier median, push), and "all other measures" |
| `distressLines` | Band `D` only: the distress lines crossed |
| `history` | 8 quarters, oldest first: `ncl` non-current loans %, `t1` Tier 1 leverage %, `roa` ROA %, `ni` quarterly net income % of assets, `eff` efficiency %; `null` where not reported |
| `quarters` | The 8 quarter labels (`2024 Q3` …) |
| `complaints` | Matched banks only: `{quarter, count, per1bDeposits}` |
| `flags` | Any of `cblr`, `newBank`, `foreignOffice`, `recentMerger` (drives "Read with care") |
| `note` | `{text, source}`: a one-line summary **generated from the bank's call report** (net income or loss in the quarter, % of assets, total assets). Never hand-written. `null` if not reported |

## `model.json`

`version`, `registered`, `periods {train, test}`, `test {rows, positives, baseRatePct}`, `models`
(`{key, name, note, roc, pr, capturePct, brier, current?, challenger?}`), `intervals` (bootstrap),
`bands` (`{code, name, range, trainRatePct, trainN, testRatePct, testN}`), `calibration`
(`{title, predictedPct, observedPct}`), `monitoring` (PSI, stable features, backtest range and alert
line), `limitations` (`{title, text}`), `next` (`{label, title, text}`). Source: MLflow and the model
card (`ingestion/site_contract/model_v5.json` holds the figures, each traceable to `docs/model_card.md`).

## `pipeline.json`

`datasets` (`{key, label, bronze, silver, gold}` rows added per layer in the latest successful run),
`marts`, `complaintMatchPct`, `runs` (the last 12 from `OPS.HEALTH_RUNS_LAST_12`: `date, state,
durationSeconds, databricksMinutes, credits, banksScored, resumed, earlierAttempts`; a run resumed after
a fix is one entry timed from its first start, `resumed: true`, with each earlier attempt as
`{state, failedTasks, durationSeconds}`, from the health view's `RESUMED` / `EARLIER_ATTEMPTS`), `latest` (`dbtModelsOk, dbtTestsPass,
dbtTestsWarn, dbtTestsFail, databricksMinutes, durationSeconds`; tests total = pass + warn + fail). A run without a
recorded duration has `durationSeconds: null` and shows "not recorded".

## Dollar amounts: one rounding rule

`$X.Xbn` from $1bn, whole millions from $10m, `$X.Xm` from $1m, whole thousands below; halves round
up. Implemented once for the pages (`money()` in `src/lib/site.ts`) and once for generated text
(`money_m()` in the data builder), so a bank's assets read the same everywhere ($6.5m, not $6m/$7m).

## Safety checks on every file

- No account identifiers or secrets: the CI scan rejects workspace hosts, account locators, OCIDs,
  IPs, keys and tokens in any JSON.
- Every file validates against its schema, or the build fails.

## Where the data comes from (Stage B)

| Data | Today | Stage B |
|---|---|---|
| Probability, band, reasons, waterfall, distress lines, 8-quarter history, complaints | `watchlist.json` + `waterfalls/` (schema 5) | reshaped by `scripts/export_site_data.py` |
| Quarterly net income history (`ni`) | `MART_BANK_SCORECARD` | added to the export |
| `pushes`, `driver` | computed from the waterfall in `build_fixtures.py` | same rule in the exporter (no scoring change) |
| Band counts by year, model metrics, calibration | MLflow (v5), model card | `overview.json`, `model.json` |
| Recent runs, layer counts | `OPS.HEALTH_RUNS_LAST_12`, `PIPELINE_RUNS` | `pipeline.json` |
