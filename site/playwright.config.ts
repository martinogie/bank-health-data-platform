import { defineConfig } from "@playwright/test";

// Runs against the built site (dist/), served under /bankhealth with the real response headers
// from public/_headers (CSP and the other security headers), as Cloudflare Pages will serve it.
export default defineConfig({
  testDir: "tests",
  timeout: 30_000,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:4321", browserName: "chromium" },
  webServer: {
    command: "node scripts/serve-dist.mjs 4321",
    url: "http://localhost:4321/bankhealth/",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
