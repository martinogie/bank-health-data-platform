// Bank Health site: served at finance.martinsogiekpolor.com/bankhealth (site/PLAN.md).
// Static output. Pages read their numbers from /bankhealth/data/*.json at runtime, so a weekly
// data refresh never needs a rebuild (the data can be served from R2 by the sector router).
import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://finance.martinsogiekpolor.com",
  base: "/bankhealth",
  output: "static",
  trailingSlash: "ignore",
  build: { format: "directory" },
  // No analytics, no tracking, no cookies: nothing to configure here on purpose.
  // (Astro's own telemetry is switched off with ASTRO_TELEMETRY_DISABLED=1 in every build.)
  devToolbar: { enabled: false },
});
