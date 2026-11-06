# Website plan (refreshed 6 Nov 2026) — DRAFT for Martins' OK

Bank Health's public site, as one project of a **sector-organised portfolio**. Design reference:
`C:\Users\marti\projects\bank-health-website-design\` (5 pages, approved 25 Oct). Build prompt:
`WEBSITE_BUILD_PROMPT.md` there. Data contract: [data/SCHEMA.md](data/SCHEMA.md). Branch
`feature/website` (worktree `C:\Users\marti\projects\bank-health-website`); nothing is pushed or
merged without asking, and nothing public (DNS, Cloudflare, deploys) happens without approval.

## 1. The portfolio: domains → sites

```
                          martinsogiekpolor.com  (DNS on Cloudflare)
                                     │
   ┌─────────────────────────────────┼──────────────────────────────────────────┐
   │                                 │                                          │
 martinsogiekpolor.com      finance.martinsogiekpolor.com              (later, not built now)
 home: bio, sectors,        sector subdomain                           health. / it. / energy.
 project cards              │                                          same pattern as finance
   │                        ▼
   │               Worker "sector-router-finance"
   │                 /                → finance lobby (portfolio site)
   │                 /bankhealth/*    → Bank Health site
   │                 /<project>/*     → that project's site (added per project)
   │                        │                         │
   ▼                        ▼                         ▼
 Pages project        Pages project              Pages project
 "portfolio"          "portfolio" (/finance/)    "bankhealth"
 repo martinogie/portfolio-site                  repo martinogie/bank-health-data-platform (site/)
 home + all sector lobbies                       built with base '/bankhealth'
```

- **martinsogiekpolor.com**: the home page (brief bio, the sectors, project cards). Served directly
  by the `portfolio` Pages project as its custom domain.
- **finance.martinsogiekpolor.com**: a small Worker owns the subdomain. `/` (and `/` paths that
  aren't a project) go to the finance lobby page inside the `portfolio` site; `/bankhealth/*` goes
  to the `bankhealth` Pages site. Visitors only ever see `finance.martinsogiekpolor.com/...` URLs.
- **One Pages project per project site** (Bank Health now), deployed on its own: a Bank Health
  deploy never rebuilds the home or lobby pages, and the other way round.
- **Only the finance sector exists now.** No pages or DNS records for health, IT or energy until a
  real project needs them (section 6 says how to add one).

## 2. Repositories and the shared design system

| Repo | Holds | Deploys to |
|---|---|---|
| `martinogie/portfolio-site` (proposed name) | `packages/design/` (the design system), `site/` (home + `/finance/` lobby; later one lobby per sector), `workers/sector-router/` (the router, one config per sector) | Pages project `portfolio`; one Worker per sector |
| `martinogie/bank-health-data-platform` (this repo) | the Bank Health site in `site/` (Astro, base `/bankhealth`) | Pages project `bankhealth` |

**The design system** (fonts Newsreader + Inter, colour tokens, header and footer, BandChip-style
components, page layout) lives in `portfolio-site/packages/design/`, versioned with git tags
(`design-v1.0.0`, …). Each project site keeps a **pinned copy** (`site/src/design/`, with a `VERSION`
file), updated with one documented command when a new design version is wanted. Why a copy and not
a package: no registry, no install token, and a project site never breaks or redeploys because the
portfolio changed. CI checks the copy matches its `VERSION`.

**Accent per sector:** the tokens are shared; each site sets `--accent` once. Finance: `#8E1B2E`
(the Bank Health burgundy). Other sectors pick theirs when they are created.

**Personal text:** the bio and any personal or career text are placeholders marked
**[MARTINS TO WRITE]** until you write them. Nothing is invented about you, your jobs or your
credentials; project cards use only what's in each project's README.

## 3. Cloudflare: how it's served, limits and cost

| Piece | Free-tier limit (Cloudflare docs, 6 Nov 2026) | This portfolio's use |
|---|---|---|
| Pages | 500 builds/month; 1 build at a time; 20 min per build; **20,000 files per site**; 25 MiB per file; 100 custom domains per project; 100 projects per account; no bandwidth or request cap listed for static assets | 2 projects now. Bank Health: ~4,300 bank files + pages ≈ 4,400 files (22% of the cap; see data refresh below) |
| Workers | **100,000 requests/day** (resets 00:00 UTC); 10 ms CPU per request; 100 Workers per account | 1 router; every request on `finance.` passes through it (a page view ≈ 5–10 requests), so roughly 10,000 page views a day before the cap |
| DNS | free zone | apex + `finance` records |

**Cost: $0** on the free tiers for a portfolio's traffic. If the router ever passed 100,000
requests a day, Workers Paid is **$5/month** (price from Cloudflare's pricing page; confirm at
setup). The domain registration itself is your existing cost.

**Weekly data refresh without rebuilding anything else** (decision for Stage B):
- **(a) Recommended: data in Cloudflare R2.** The weekly DAG task uploads the JSON files to a bucket
  (`bankhealth-data`); the router serves `/bankhealth/data/*` from it. The Bank Health site itself
  then only deploys when its code changes, and the data refresh touches nothing else. The R2 key
  goes into OCI Vault like the other secrets. R2 free tier (Cloudflare docs, 6 Nov 2026): 10 GB
  stored per month, 1 million writes and 10 million reads per month, free egress. The weekly upload
  is ~4,300 files (~17,000 writes a month) of ~10 MB in total.
- (b) Redeploy the `bankhealth` Pages project each week with the new data (direct upload from the
  VM). Simpler, but each week is a full site deploy, and it keeps the site near the file cap as
  banks are added.

## 4. Bank Health site (Stage A, B)

**Stack:** Astro, static output, **base `/bankhealth`**; plain CSS using the design system;
small TypeScript islands; inline SVG charts; no tracking, no cookies. The current root
`index.html` and `watchlist.html` (GitHub Pages) stay untouched until launch.

| Page (under `finance.martinsogiekpolor.com/bankhealth`) | Data | Components |
|---|---|---|
| `/` Overview | `overview.json` | BandSummary, BandsByYearChart (toggle H/E/W), Top20Table (filter all / credit / earnings; driver chips), HowToRead |
| `/watchlist` | `banks_index.json`, `banks/<cert>.json` | BankSearch (all banks), WatchlistStore (localStorage in try/catch + `?certs=`), BankCard (band, probability, reasons, sparklines, "what changed"), CopyLink, CsvExport |
| `/bank/?cert=NNNN` | `banks/<cert>.json` | BankHeader, ProbabilityPanel, Reasons, WhatIfBars, WaterfallChart, HistoryChart (tabs: NCL, Tier 1, ROA, net income), Flags |
| `/methods` | `model.json` | MetricsTable (challenger toggle), BandsTable, CalibrationChart, Monitoring, Limitations |
| `/pipeline` | `pipeline.json` | FlowDiagram, LayerCounts, ChecksList, RunsTable |
| `/dbt/` (at launch) | the checked dbt docs build | — |

Every page: works at 375 px with no sideways scroll, real buttons and links, WCAG AA contrast,
`aria-pressed` on toggles, a text alternative for every chart.

**What changed since the prompt was saved (25 Oct):** SHAP reasons, waterfalls and distress lines
exist (the bank files absorb today's per-bank waterfall files); model monitoring and the run log's
health view feed Methods and Pipeline; the dbt docs are built and checked. Not needed after all:
previous-quarter scores (the watchlist's "what changed" compares ratios from the history) and the
30–89 days past-due series (not in the design).

**Stage A, against fixtures (no backend change):** fixture JSON following the schema with the
design's real values, marked `"fixture": true` (a "preview data" label shows); Playwright at 1440
and 375 px, axe (0 serious), Lighthouse (Performance ≥ 90, Accessibility ≥ 95), schema validation;
CI job `.github/workflows/site.yml`; screenshots in `docs/images/site/`.

**Stage B, real data (backend changes, dev first):** `05_score` adds the what-if probabilities
and the driver; `scripts/export_site_data.py` + a DAG task after the top-20 check write the five
files (replacing the watchlist export at launch; `watchlist.json` stays until then).

## 5. Order

1. **Bank Health Stage A** (fixtures) — first.
2. **portfolio-site**: design system, home page, finance lobby (minimal, same design), the router
   Worker code — after you've seen this plan; built and tested locally, nothing deployed.
3. Bank Health Stage B (real data).
4. **Launch** (each step with your approval): DNS on Cloudflare, the Pages projects, the router,
   R2 (if chosen), custom domains, then retire the GitHub Pages copies.

## 6. Adding things later (short, documented steps)

**A new project in an existing sector** (e.g. `finance.martinsogiekpolor.com/<project>`):
1. Build the project site with base `/<project>` and the pinned design copy.
2. Create a Pages project for it.
3. Add one line to the sector's router config (`/<project>/*` → its Pages site) and deploy the Worker.
4. Add its card to the lobby (and the home page) in `portfolio-site`.

**A new sector** (e.g. `health.martinsogiekpolor.com`):
1. In `portfolio-site`: add the lobby page `/health/` and its accent colour.
2. Add the router config for `health` and deploy a Worker on `health.martinsogiekpolor.com`.
3. Add the DNS record (proxied) for `health`.
4. Add the sector to the home page. Only when its first project is ready.

## Decisions for Martins

1. **What-if and driver** (Stage B): **(a)** as the design says, set the group to the tier median and
   score again (2 extra predictions per bank in `05_score`) — recommended — or (b) from the SHAP
   values already computed.
2. **The data schema** ([data/SCHEMA.md](data/SCHEMA.md)), including one file per bank.
3. **The portfolio architecture above**: repo name `martinogie/portfolio-site`, the pinned design
   copy, one router Worker per sector.
4. **Weekly data refresh**: (a) R2 — recommended — or (b) weekly redeploy.
5. Start **Bank Health Stage A** now.
