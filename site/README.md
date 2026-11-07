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
FDIC call report. Model figures (`ingestion/site_contract/model_v5.json`) are copied from the model card.

## Toolchain

One Node and npm version everywhere: **Node 26.13.0** (`.nvmrc`) and **npm 11.20.0**
(`packageManager` and `engines` in `package.json`; `.npmrc` sets `engine-strict`, so npm refuses
to install with anything else). It's what's on the dev PC and in the CI image
(`mcr.microsoft.com/playwright:v1.65.0-noble`); CI fails if the image ever drifts.

- **Install with `npm ci`, never `npm install`.** `npm ci` installs exactly `package-lock.json` and
  never rewrites it. Use `npm install <package>` only when deliberately changing a dependency, and
  commit the lockfile change in the same pull request.
- **Telemetry off.** Set `ASTRO_TELEMETRY_DISABLED=1` (CI does; on the PC, once:
  `npx astro telemetry disable`).
- **Windows and Docker share `site/node_modules`.** Each installs its own platform binaries
  (Rollup, esbuild), so after switching between them, run `npm ci` again. The lockfile doesn't
  change.

## Run it

On the PC (PowerShell, from `site/`):

```powershell
npm.cmd ci                 # install exactly the lockfile
npm.cmd run dev            # http://localhost:4321/bankhealth/  (this computer only)
npm.cmd run build          # build to site/dist
npm.cmd run preview        # serve the build at http://localhost:4321/bankhealth/
```

`dev` and `preview` listen on localhost only. `npm.cmd run dev:lan` and `npm.cmd run preview:lan`
open them to the local network (e.g. to try the site on a phone); stop them when done.

In Docker, as CI runs it (Git Bash, from the repository root):

```sh
IMG=mcr.microsoft.com/playwright:v1.65.0-noble
RUN="docker run --rm --ipc=host -v $(pwd -W):/repo -w /repo/site -e ASTRO_TELEMETRY_DISABLED=1 -e CI=1"
MSYS_NO_PATHCONV=1 $RUN $IMG npm ci                               # install exactly the lockfile
MSYS_NO_PATHCONV=1 $RUN $IMG node src/design/check-design.mjs     # pinned design copy unchanged
MSYS_NO_PATHCONV=1 $RUN $IMG node scripts/validate-data.mjs       # data files match their schemas
MSYS_NO_PATHCONV=1 $RUN $IMG npx astro build                      # build to site/dist
MSYS_NO_PATHCONV=1 $RUN $IMG npx playwright test                  # browser tests + axe + screenshots
MSYS_NO_PATHCONV=1 $RUN $IMG sh scripts/lighthouse.sh             # Lighthouse budget
```

## Dependency audit (7 Nov 2026)

`npm audit` found 5 issues, all in **build tooling** that comes with Astro. **Nothing from
`node_modules` ships in the built site**: `dist/` holds only this site's own scripts (about 31 KB),
fonts, data and HTML.

| Package | Severity | Status |
|---|---|---|
| esbuild (dev server could read files on Windows) | low | **Fixed**: pinned to 0.28.2 with an npm `overrides` entry. The dev server is also localhost-only now |
| sharp (image libraries) | high | **Fixed**: pinned to 0.35.5 with `overrides`. The site doesn't process images anyway |
| astro (XSS, SSRF, image and server-mode issues) | critical | **Open until Astro 7.** Every Astro 5 release is affected. None of the affected features are used here: static output only, no `define:vars`, view transitions, server islands, image optimisation or spread attributes |
| zod, zod-to-ts (memory use on oversized input) | high | **Open until Astro 7.** Used only at build time, to check this site's own config; no visitor input reaches it |

No `npm audit fix --force`: that would jump to Astro 7, a breaking upgrade.

**Later task (its own pull request): upgrade to Astro 7**, which clears the remaining 3, then
re-run every test, Lighthouse and the screenshots.

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
