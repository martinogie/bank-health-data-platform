import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const B = "/bankhealth";
const PAGES = [
  ["overview", `${B}/`],
  ["watchlist", `${B}/watchlist/`],
  ["bank", `${B}/bank/?cert=24045`],
  ["methods", `${B}/methods/`],
  ["pipeline", `${B}/pipeline/`],
] as const;
const WIDTHS = [1440, 375];

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator("[data-asof]")).not.toHaveAttribute("aria-busy");
  await expect(page.locator("[data-asof]")).toContainText("Data as of");
}

for (const [name, path] of PAGES) {
  for (const width of WIDTHS) {
    test(`${name} at ${width}px: renders, no errors, no sideways scroll, accessible`, async ({ page }) => {
      const errors: string[] = [];
      const external: string[] = [];
      page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
      page.on("pageerror", (e) => errors.push(e.message));
      page.on("request", (r) => { if (!r.url().startsWith("http://localhost:4321")) external.push(r.url()); });
      await page.setViewportSize({ width, height: 900 });
      await page.goto(path);
      await settle(page);
      await expect(page.locator("h1:visible").first()).toBeVisible();
      expect(errors, "console errors").toEqual([]);
      expect(external, "requests to other sites (no tracking, no CDNs)").toEqual([]);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, "horizontal overflow in px").toBeLessThanOrEqual(0);
      expect(await page.context().cookies(), "no cookies").toEqual([]);
      const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${v.id}: ${v.nodes.length}`), "serious accessibility violations").toEqual([]);
      await page.screenshot({ path: `../docs/images/site/${name}-${width}.png`, fullPage: true });
    });
  }
}

test("overview: band toggle and driver filter work; preview label shows", async ({ page }) => {
  await page.goto(`${B}/`);
  await settle(page);
  await expect(page.locator("[data-preview]")).toBeVisible();
  await page.getByRole("button", { name: "Elevated" }).click();
  await expect(page.getByRole("button", { name: "Elevated" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-series-caption]")).toContainText("Elevated band");
  await page.getByRole("button", { name: "Earnings signals" }).click();
  const chips = page.locator("[data-top] .driver");
  await expect(chips.first()).toBeVisible();
  for (const t of await chips.allTextContents()) expect(t).toBe("Earnings");
  await expect(page.locator("[data-top] tr")).toHaveCount(await chips.count());
});

test("watchlist: search all banks, add, remove, reload keeps the list", async ({ page }) => {
  await page.goto(`${B}/watchlist/?certs=19629`);
  await settle(page);
  await expect(page.locator(".bank-card")).toHaveCount(1);
  await page.getByLabel("Find a bank by name or state").fill("banc of cal");
  await page.locator("#search-results").getByRole("button", { name: /Add/ }).first().click();
  await expect(page.locator(".bank-card")).toHaveCount(2);
  await expect(page).toHaveURL(/certs=19629%2C24045|certs=19629,24045/);
  await page.reload();
  await settle(page);
  await expect(page.locator(".bank-card")).toHaveCount(2);
  await page.getByRole("button", { name: "Remove International Bank of Commerce from watchlist" }).click();
  await expect(page.locator(".bank-card")).toHaveCount(1);
  await expect(page.locator("[data-status]")).toHaveText("Removed");
});

test("watchlist: a ?certs= link renders in a fresh browser; CSV export downloads", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(`${B}/watchlist/?certs=24045,13986,59245`);
  await settle(page);
  await expect(page.locator(".bank-card")).toHaveCount(3);
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Export CSV" }).click()]);
  expect(download.suggestedFilename()).toBe("bank-health-watchlist.csv");
  const csv = readFileSync(await download.path()!, "utf-8");
  expect(csv.split("\r\n")).toHaveLength(4);
  expect(csv).toContain("Banc of California");
  await ctx.close();
});

test("watchlist works with storage blocked", async ({ browser }) => {
  const ctx = await browser.newContext();
  await ctx.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new Error("blocked"); } });
  });
  const page = await ctx.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`${B}/watchlist/?certs=24045`);
  await settle(page);
  await expect(page.locator(".bank-card")).toHaveCount(1);
  expect(errors).toEqual([]);
  await ctx.close();
});

test("bank page: from a cert, tabs switch, rating line present", async ({ page }) => {
  await page.goto(`${B}/bank/?cert=24045`);
  await settle(page);
  await expect(page.locator("h1[data-name]")).toHaveText("Banc of California");
  await page.getByRole("button", { name: "Quarterly net income" }).click();
  await expect(page.getByRole("button", { name: "Quarterly net income" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-chart-title]")).toContainText("Net income in the quarter");
  await expect(page.getByText("Push from earnings")).toBeVisible();
  await expect(page.getByText("A model estimate from public FDIC data, not a supervisory rating.")).toBeVisible();
  await expect(page.getByText(/about a third of complaints match a bank/)).toBeVisible();
});

test("bank page: a bank outside the preview, and no cert at all", async ({ page }) => {
  await page.goto(`${B}/bank/?cert=628`);
  await settle(page);
  await expect(page.locator("[data-missing]")).toBeVisible();
  await page.goto(`${B}/bank/`);
  await settle(page);
  await page.getByLabel("Bank name or state").fill("genesis");
  await expect(page.locator("#find-results a").first()).toBeVisible();
});

test("watchlist page carries the rating line and the complaint caveat", async ({ page }) => {
  await page.goto(`${B}/watchlist/`);
  await settle(page);
  await expect(page.getByText("A model estimate from public FDIC data, not a supervisory rating.")).toBeVisible();
  await expect(page.getByText(/not model inputs/)).toBeVisible();
  await expect(page.getByText("Built with AI-assisted coding; the design, analysis and review are my own.")).toBeVisible();
});

// The phrase is assembled here so it never appears written out in the repo.
const REVIEW_CLAIM = new RegExp(["A", "I"].join("") + "[- ]" + "review", "i");
test("built site: no wording that claims a review by AI", () => {
  const walk = (d: string): string[] => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
  for (const f of walk("dist").filter((f) => /\.(html|js|json)$/.test(f))) {
    expect(readFileSync(f, "utf-8"), f).not.toMatch(REVIEW_CLAIM);
  }
});
