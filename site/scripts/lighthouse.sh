#!/bin/sh
# Lighthouse budget for every page: Performance >= 90, Accessibility >= 95 (mobile defaults).
# Run inside the Playwright image from site/, after `npx astro build`.
# The built site is served compressed (gzip), as Cloudflare Pages serves it, and each page's
# score is the median of 3 runs after a warm-up, because single Lighthouse runs are noisy.
set -e
CHROME_PATH=$(ls -d /ms-playwright/chromium-*/chrome-linux*/chrome | head -1); export CHROME_PATH
node scripts/serve-dist.mjs 4321 &
SERVER=$!; trap 'kill $SERVER' EXIT
for i in $(seq 1 30); do curl -sf http://127.0.0.1:4321/bankhealth/ >/dev/null && break; sleep 1; done
npx --yes lighthouse@12.8.2 --version >/dev/null   # download once, outside the timed runs
fail=0
for p in "" watchlist/ "bank/?cert=24045" methods/ pipeline/; do
  url="http://127.0.0.1:4321/bankhealth/$p"
  curl -sf "$url" >/dev/null
  scores=""
  for run in 1 2 3; do
    npx --yes lighthouse@12.8.2 "$url" --quiet --only-categories=performance,accessibility \
      --chrome-flags="--headless=new --no-sandbox" --output=json --output-path=/tmp/lh.json >/dev/null 2>&1
    scores="$scores $(node -e 'const r=require("/tmp/lh.json");console.log(Math.round(r.categories.performance.score*100)+":"+Math.round(r.categories.accessibility.score*100))')"
  done
  perf=$(echo $scores | tr ' ' '\n' | cut -d: -f1 | sort -n | sed -n 2p)
  a11y=$(echo $scores | tr ' ' '\n' | cut -d: -f2 | sort -n | sed -n 2p)
  echo "/bankhealth/$p  performance $perf  accessibility $a11y  (runs:$scores)"
  [ "$perf" -ge 90 ] && [ "$a11y" -ge 95 ] || fail=1
done
exit $fail
