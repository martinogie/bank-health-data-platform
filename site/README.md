# Bank Health website

The public site for Bank Health, served at `finance.martinsogiekpolor.com/bankhealth` (not live
yet). Five pages: Overview, Watchlist, Bank detail, Model & methods, Pipeline. Plan:
[PLAN.md](PLAN.md). Data contract: [data/SCHEMA.md](data/SCHEMA.md).

- **Astro**, static output, built with base `/bankhealth`. Plain CSS (`src/design/`), small
  TypeScript scripts, inline SVG charts, self-hosted fonts.
- Pages fetch their numbers at runtime from `/bankhealth/data/*.json`, so a weekly data refresh
  needs no rebuild.
- No tracking, no cookies, no requests to other sites. The watchlist is kept in the visitor's own
  browser (and in the share link).

## Preview data (Stage A)

`public/data/` holds **preview data**: real figures from the 2026 Q2 scoring run, marked
`"fixture": true` so the site shows a "Preview data" label. 26 banks have a full page; the
rest appear in search with their band and probability. Rebuild it with
`python scripts/build_fixtures.py` from read-only extracts in `.fixture-inputs/` (git-ignored).
There is no hand-written text about any bank: each bank's one-line summary is generated from its
FDIC call report. Model figures (`content/model_v5.json`) are copied from the model card.

## Run it (Docker; no Node needed on the PC)

From the repository root, in Git Bash:

```sh
IMG=mcr.microsoft.com/playwright:v1.65.0-noble
RUN="docker run --rm --ipc=host -v $(pwd -W):/repo -w /repo/site -e ASTRO_TELEMETRY_DISABLED=1 -e CI=1"
MSYS_NO_PATHCONV=1 $RUN $IMG npm ci                               # install
MSYS_NO_PATHCONV=1 $RUN $IMG node scripts/validate-data.mjs       # data files match their schemas
MSYS_NO_PATHCONV=1 $RUN $IMG npx astro build                      # build to site/dist
MSYS_NO_PATHCONV=1 $RUN $IMG npx playwright test                  # browser tests + axe + screenshots
MSYS_NO_PATHCONV=1 $RUN $IMG sh scripts/lighthouse.sh             # Lighthouse budget
MSYS_NO_PATHCONV=1 $RUN -p 4321:4321 $IMG npx astro preview --host 0.0.0.0   # view at http://localhost:4321/bankhealth/
```

Identifier scan (also run in CI, where it uses the generic patterns only): from the repo root,
`python scripts/check_public_docs.py site/public/data/*.json site/public/data/banks/*.json` with
the `.env` values loaded in the environment, so the real account values are checked too (only
variable names are ever printed).

## Tests

| Check | What it proves | Latest result (6 Nov 2026, preview data) |
|---|---|---|
| Schemas (`scripts/validate-data.mjs`) | Every data file matches its JSON Schema | 30 files, 0 invalid |
| Identifier scan (`scripts/check_public_docs.py`) | No account identifiers, hostnames, addresses, keys or emails in any data file or built page | all data files and built pages clean |
| Playwright (`tests/site.spec.ts`) | Each page at 1440 and 375 px: no console errors, no outside requests, no sideways scroll, no cookies, no serious axe issues; the toggles, filters, watchlist, share link, CSV, blocked storage, the rating line and complaint caveat; no wording that claims a review by AI; the bank chart draws bars from zero with the distress line on the same scale; pushes that pull a score down draw left of centre; phone quarter labels do not overlap; pipeline durations and dbt counts | 22 passed |
| Lighthouse (`scripts/lighthouse.sh`) | Performance ≥ 90 and Accessibility ≥ 95 on every page (mobile, median of 3, served compressed as on Cloudflare) | Performance 97–100, Accessibility 100 on all 5 pages |

CI: [.github/workflows/site.yml](../.github/workflows/site.yml) runs all four on every change to
`site/`. Screenshots: [docs/images/site/](../docs/images/site/).
