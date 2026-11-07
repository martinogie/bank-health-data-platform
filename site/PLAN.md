# Website plan (agreed 6 Nov 2026)

Bank Health's public site, as one project of a **sector-organised portfolio**. Design reference:
the approved 5-page design (25 Oct) and its build prompt, kept outside this repo. Data contract:
[data/SCHEMA.md](data/SCHEMA.md). Branch `feature/website`; nothing is pushed or merged without
asking, and nothing public (DNS, Cloudflare, deploys) happens without approval.

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

**The design system** (fonts Newsreader + Inter, colour tokens, sector accents, base styles,
header and footer) lives in `portfolio-site/packages/design/`, versioned with git tags
(`design-v1.0.0`, …). Each project site keeps a **pinned copy**: Bank Health pins
**design-v1.0.1, Finance** in `site/src/design/` (`DESIGN.json` lists the version and a SHA-256 per
file), written by `portfolio-site/scripts/pin-design.mjs`. Why a copy and not a package: no
registry, no install token, and a project site never breaks or redeploys because the portfolio
changed. CI (`site.yml`) runs `src/design/check-design.mjs`, which fails if the copy was edited.
Bank Health's own styles (risk bands, chips, bars) are in `site/src/styles/bankhealth.css`.

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

**Weekly data refresh: Cloudflare R2 (decided 6 Nov, 4a).** The site only redeploys when its code
changes; the data refresh touches nothing else. R2 free tier (Cloudflare docs, 6 Nov 2026): 10 GB
stored per month, 1 million writes and 10 million reads per month, free egress. One week is ~4,300
files (~10 MB), so 5 weeks kept ≈ 50 MB and ~17,000 writes a month.

Conditions (Martins, 6 Nov):
1. **Publish only after every pipeline check passes**: freshness, volumes, data contracts, dbt
   tests and the top-20 release check. The upload is the last DAG task and never runs after a
   failure.
2. **Versioned weeks, one pointer.** Each run writes to its own prefix
   (`bankhealth-data/runs/<run date>/…`), then rewrites one small file, `current.json`
   (`{"run": "<run date>"}`). The router reads the pointer and serves `/bankhealth/data/*` from that
   prefix. A half-finished upload is never visible.
3. **Keep at least the previous 4 weeks.** Older prefixes are deleted by the same task only after the
   new pointer is in place. **Rollback = point `current.json` back to an earlier run** (one
   documented step in the runbook; no rebuild).
4. **The R2 API token is a new secret.** Martins creates it (write access to this bucket only) and
   stores it in OCI Vault; the VM fetches it like the other secrets; it's listed in
   `ops/credentials.yml` with a rotation date. Nobody else sees or pastes it.
5. **"Data as of <FDIC quarter> · updated <run date>"** shows in the site header from each file's
   `dataAsOf` (built in Stage A).
6. **Billing:** if Cloudflare asks for a payment method to enable R2, I tell you before anything is
   set up, with the steps to add a billing notification (Cloudflare dashboard → Notifications →
   Add → Billing / Usage-based billing, sent to your email) so any charge is flagged early.

## 4. Bank Health site (Stage A, B)

**Stack:** Astro, static output, **base `/bankhealth`**; plain CSS using the design system;
small TypeScript islands; inline SVG charts; no tracking, no cookies. The current root
`index.html` and `watchlist.html` (GitHub Pages) stay untouched until launch.

| Page (under `finance.martinsogiekpolor.com/bankhealth`) | Data | Components |
|---|---|---|
| `/` Overview | `overview.json` | BandSummary, BandsByYearChart (toggle H/E/W), Top20Table (filter all / credit / earnings; driver chips), HowToRead |
| `/watchlist` | `banks_index.json`, `banks/<cert>.json` | BankSearch (all banks), WatchlistStore (localStorage in try/catch + `?certs=`), BankCard (band, probability, reasons, sparklines, "what changed"), CopyLink, CsvExport |
| `/bank/?cert=NNNN` | `banks/<cert>.json` | BankHeader, ProbabilityPanel, Reasons, PushBars (loan quality / earnings, from SHAP), WaterfallChart, HistoryChart (tabs: NCL, Tier 1, ROA, net income), ReadWithCare, Complaints |
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

**Stage B, real data (dev first; no scoring change):** `scripts/export_site_data.py` writes the five
files (pushes and driver from the existing SHAP waterfall, as in the fixtures), and a DAG task after
every check uploads them to R2 under the conditions above (replacing the watchlist export at launch;
`watchlist.json` stays until then). `05_score` is not touched during the sign-off runs (9, 16 and
23 Nov).

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

## Decisions (Martins, 6 Nov 2026: "1b, 2 OK, 3 OK, 4a, start A")

1. **What-if and driver: (b) for launch**, from the existing SHAP values, with honest labels ("Push
   from loan quality", "Push from earnings"); the driver chip is the larger group. No scoring change
   during the sign-off runs. The median re-score (a) is on the v6 list in the model card.
2. **Data schema: OK**, one file per bank, past-due series dropped ([data/SCHEMA.md](data/SCHEMA.md)).
3. **Architecture: OK**: `martinogie/portfolio-site`, pinned design copy, one router Worker per
   sector, Finance accent `#8E1B2E`.
4. **Weekly data refresh: (a) R2**, with the six conditions in section 3.
5. **Stage A started**: preview data, base `/bankhealth`, no backend change.

Must-haves on the site: the "Model estimate from public FDIC data, not a supervisory rating" line
on the watchlist and bank pages; the complaint caveats; no tracking or cookies; a CI check that no
account identifiers or secrets appear in any JSON; nothing that claims a review by AI (the footer may say
"Built with AI-assisted coding; the design, analysis and review are my own.").
