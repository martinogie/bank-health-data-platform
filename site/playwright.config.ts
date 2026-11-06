import { defineConfig } from "@playwright/test";

// Runs against the built site (astro preview serves dist/ under /bankhealth).
export default defineConfig({
  testDir: "tests",
  timeout: 30_000,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:4321", browserName: "chromium" },
  webServer: {
    command: "npx astro preview --host 0.0.0.0 --port 4321",
    url: "http://localhost:4321/bankhealth/",
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
