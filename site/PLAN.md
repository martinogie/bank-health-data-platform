# Website plan (refreshed 6 Nov 2026) — DRAFT for Martins' OK

bank.martinsogiekpolor.com. Design reference: `C:\Users\marti\projects\bank-health-website-design\`
(5 pages, approved 25 Oct). Build prompt: `WEBSITE_BUILD_PROMPT.md` there. Data contract:
[data/SCHEMA.md](data/SCHEMA.md). Branch `feature/website` (worktree
`C:\Users\marti\projects\bank-health-website`); nothing is pushed or merged without asking.

## What changed since the prompt was saved (25 Oct)

- Reasons are **SHAP** now (compared with the same asset tier); each bank has a **waterfall** (top
  8 pushes) and, if already in distress, the **distress lines** it crossed.
- The waterfalls already live in one small file per bank (schema 5): the bank files below follow
  the same idea and absorb them.
- Model monitoring (PSI, backtest) and the run log's health view exist: the Methods and Pipeline
  pages read them.
- dbt docs are built and checked; they go to `/dbt/` at launch.
- Not needed after all: previous-quarter scores (the watchlist's "what changed" compares ratios
  from the history) and the 30–89 days past-due series (not in the design).

## Stack (as in the prompt)

Astro, static output; plain CSS with the design tokens as variables; small TypeScript islands; no
UI framework, no tracking, no cookies. Newsreader + Inter, self-hosted. Charts as inline SVG from
data. Source in `site/`, built to `site/dist`. The current `index.html` and `watchlist.html` stay
untouched until launch.

## Pages and components

| Page | Data | Components |
|---|---|---|
| `/` Overview | `overview.json` | BandSummary (counts + meaning), BandsByYearChart (toggle H/E/W), Top20Table (filter: all / credit / earnings; driver chips), HowToRead |
| `/watchlist` | `banks_index.json`, `banks/<cert>.json` | BankSearch (all banks), WatchlistStore (localStorage in try/catch + `?certs=`), BankCard (band, probability, reasons, sparklines, "what changed"), CopyLink, CsvExport |
| `/bank/?cert=NNNN` | `banks/<cert>.json` | BankHeader, ProbabilityPanel (band, rank, peer percentile), Reasons, WhatIfBars, WaterfallChart, HistoryChart (tabs: NCL, Tier 1, ROA, net income), Flags/notes |
| `/methods` | `model.json` | QuestionPanel, MetricsTable (challenger toggle), BandsTable, CalibrationChart, Monitoring, Limitations |
| `/pipeline` | `pipeline.json` | FlowDiagram, LayerCounts, ChecksList, RunsTable |

Shared: Layout (header, nav, footer), BandChip, ProbabilityBar, Sparkline, PreviewDataBadge.
Every page: works at 375 px with no sideways scroll, real buttons and links, WCAG AA contrast,
`aria-pressed` on toggles, a text alternative for every chart.

## Stages

**A. Site against fixtures** (no backend change). Fixture JSON that follows the schema, using the
real values in the design files, marked `"fixture": true`. Playwright (1440 and 375 px: renders, no
console errors, no sideways scroll, toggles, search, add/remove, `?certs=`, bank page from a cert),
axe (0 serious), Lighthouse (Performance ≥ 90, Accessibility ≥ 95), schema validation, CI job
`.github/workflows/site.yml`, screenshots in `docs/images/site/`.

**B. Real data** (backend changes, tested in dev first):
1. `05_score`: the what-if probabilities (credit and earnings at the tier median) and the driver,
   stored with the scores. Prod-touching: one dev run, then a manual prod run.
2. `scripts/export_site_data.py` + a DAG task after the top-20 check, writing the five files. It
   replaces the watchlist export at launch; `watchlist.json` stays until then.

**C. Launch** (separate, approved step): hosting, the custom domain and DNS, `/dbt/`, replacing the
current pages. Nothing of this is done before your OK.

## Decisions for Martins

1. **What-if and driver:** compute them as the design says (set the group to the tier median and
   score again; 2 extra predictions per bank in `05_score`) — recommended — or derive them from the
   SHAP values already computed (no scoring change, but "the push from earnings" rather than "if
   earnings were at the median").
2. **The schema above**, including dropping the past-due series and keeping one file per bank.
3. Start with **Stage A** (fixtures, no backend change) now.
