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

test("watchlist with live-shaped data (no preview list): full cards load, no preview wording", async ({ page }) => {
  await page.route("**/data/banks_index.json", async (route) => {
    const body = await (await route.fetch()).json();
    delete body.preview;
    body.fixture = false;
    await route.fulfill({ json: body });
  });
  const ix = await (await page.request.get(`${B}/data/banks_index.json`)).json();
  await page.goto(`${B}/watchlist/?certs=${ix.preview[0]}`);
  await settle(page);
  await expect(page.locator(".sparks").first()).toBeVisible();
  await expect(page.locator("main")).not.toContainText("preview has full files");
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

// Reads the bank chart's geometry: the zero line, the distress line and each bar (value, top, bottom).
async function chartGeometry(page: Page) {
  return page.locator("[data-chart]").evaluate((svg) => {
    const num = (e: Element | null, a: string) => (e ? parseFloat(e.getAttribute(a)!) : NaN);
    const line = svg.querySelector("[data-line-y]");
    return {
      zeroY: num(svg.querySelector("[data-zero-y]"), "data-zero-y"),
      lineY: num(line, "data-line-y"), lineV: num(line, "data-line-v"),
      bars: [...svg.querySelectorAll("rect[data-v]")].map((r) => ({ v: num(r, "data-v"), top: num(r, "y"), bottom: num(r, "y") + num(r, "height") })),
    };
  });
}

test("bank chart: bars start at zero and the distress line sits at its value on the same scale", async ({ page }) => {
  await page.goto(`${B}/bank/?cert=24045`);
  await settle(page);
  for (const tab of ["Non-current loans", "Tier 1 leverage"]) {
    await page.getByRole("button", { name: tab }).click();
    const g = await chartGeometry(page);
    expect(g.lineV, `${tab}: distress line drawn`).toBeGreaterThan(0);
    expect(g.lineY, `${tab}: distress line above the zero line`).toBeLessThan(g.zeroY - 1);
    const perUnit = (g.zeroY - g.lineY) / g.lineV; // px per percentage point, from the line
    for (const b of g.bars.filter((x) => x.v > 0)) {
      expect(Math.abs(b.bottom - g.zeroY), `${tab}: bar ${b.v} starts at zero`).toBeLessThan(0.5);
      expect(Math.abs((g.zeroY - b.top) - b.v * perUnit), `${tab}: bar ${b.v} on the line's scale`).toBeLessThan(1);
      if (b.v < g.lineV) expect(b.top, `${tab}: bar ${b.v} below the ${g.lineV}% line`).toBeGreaterThan(g.lineY);
    }
  }
});

test("bank page: pushes that pull the score down draw left of the centre line", async ({ page }) => {
  await page.goto(`${B}/bank/?cert=24045`);
  await settle(page);
  const rows = await page.locator(".wf-track[data-pp]").evaluateAll((els) => els.map((el) => {
    const mid = el.querySelector(".wf-mid")!.getBoundingClientRect(), bar = el.querySelector(".wf-bar")!.getBoundingClientRect();
    return { pp: parseFloat(el.getAttribute("data-pp")!), mid: mid.left, left: bar.left, right: bar.right };
  }));
  expect(rows.some((r) => r.pp < 0), "the sample bank has a negative push").toBe(true);
  for (const r of rows) {
    if (r.pp < 0) expect(r.right, `push ${r.pp} ends at the centre`).toBeLessThanOrEqual(r.mid + 1.5);
    else expect(r.left, `push ${r.pp} starts at the centre`).toBeGreaterThanOrEqual(r.mid - 1.5);
  }
});

test("bank chart on a phone: quarter labels don't overlap", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto(`${B}/bank/?cert=24045`);
  await settle(page);
  const boxes = await page.locator("[data-chart-q] span").evaluateAll((els) =>
    els.map((e) => { const r = document.createRange(); r.selectNodeContents(e); const b = r.getBoundingClientRect(); return { t: e.textContent, l: b.left, r: b.right }; }).filter((b) => b.t));
  expect(boxes.length).toBeGreaterThanOrEqual(4);
  for (let i = 1; i < boxes.length; i++) expect(boxes[i].l, `"${boxes[i - 1].t}" and "${boxes[i].t}"`).toBeGreaterThan(boxes[i - 1].r + 2);
});

test("pipeline: durations from the run log, and the dbt test counts", async ({ page }) => {
  await page.goto(`${B}/pipeline/`);
  await settle(page);
  const cells = await page.locator("[data-runs] td:nth-child(3)").allTextContents();
  expect(cells.length).toBeGreaterThan(0);
  for (const c of cells) expect(c.trim()).toMatch(/^(\d+ (min|s)|\d+ h \d+ min|not recorded)(from first start)?$/);
  // Resumed runs: one run "resumed after a fix", with the failed attempt still shown underneath.
  const resumed = page.locator("tr.run-group");
  expect(await resumed.count()).toBeGreaterThan(0);
  for (const row of await resumed.all()) {
    await expect(row).toContainText("resumed after a fix");
    const attempt = row.locator("xpath=following-sibling::tr[1]");
    await expect(attempt).toHaveClass(/run-attempt/);
    await expect(attempt).toContainText("Failed");
  }
  // The big number (total) and its label are separate elements; total = passed + warned + failed.
  const label = page.getByText(/^dbt tests on the warehouse tables in the latest run: \d+ passed, \d+ warned, \d+ failed$/);
  await expect(label).toBeVisible();
  const [pass, warn, fail] = ((await label.textContent())!.match(/\d+/g) ?? []).map(Number);
  await expect(label.locator("xpath=preceding-sibling::span[1]")).toHaveText(String(pass + warn + fail));
});

test("pipeline: a latest run with no Databricks minutes recorded shows a dash, never 'null'", async ({ page }) => {
  await page.route("**/data/pipeline.json", async (route) => {
    const body = await (await route.fetch()).json();
    body.latest.databricksMinutes = null;
    await route.fulfill({ json: body });
  });
  await page.goto(`${B}/pipeline/`);
  await settle(page);
  await expect(page.getByText("Databricks compute not recorded for the latest run")).toBeVisible();
  await expect(page.locator("main")).not.toContainText("null");
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

test("security headers on every response; the CSP allows no third-party source; no inline scripts", async ({ request }) => {
  for (const path of ["/bankhealth/", "/bankhealth/watchlist/", "/bankhealth/data/overview.json"]) {
    const h = (await request.get(path)).headers();
    const csp = h["content-security-policy"] ?? "";
    expect(csp, path).toContain("default-src 'self'");
    expect(csp, path).toContain("script-src 'self'");
    expect(csp, path).toContain("frame-ancestors 'none'");
    expect(csp.match(/https?:\/\/|\*/g), `${path}: no third-party or wildcard sources`).toBeNull();
    expect(h["x-content-type-options"], path).toBe("nosniff");
    expect(h["referrer-policy"], path).toBe("strict-origin-when-cross-origin");
  }
  const walk = (d: string): string[] => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
  for (const f of walk("dist").filter((f) => f.endsWith(".html"))) {
    const inline = [...readFileSync(f, "utf-8").matchAll(/<script(?![^>]*\bsrc=)[^>]*>/g)].map((m) => m[0]);
    expect(inline, `${f}: inline scripts are blocked by the CSP`).toEqual([]);
  }
  expect(readFileSync("dist/_headers", "utf-8")).toContain("Strict-Transport-Security: max-age=31536000");
});

test("pages carry a canonical link and social preview tags, and are not marked noindex", async ({ page }) => {
  await page.goto("/bankhealth/methods/");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://finance.martinsogiekpolor.com/bankhealth/methods/");
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute("content", "https://finance.martinsogiekpolor.com/bankhealth/social.png");
  await expect(page.locator('meta[name="robots"]')).toHaveCount(0);
  expect(statSync("dist/social.png").size).toBeGreaterThan(10_000);
});

// The phrase is assembled here so it never appears written out in the repo.
const REVIEW_CLAIM = new RegExp(["A", "I"].join("") + "[- ]" + "review", "i");
test("an unknown address gets the 404 page with status 404, not indexed, with a way back", async ({ page }) => {
  const res = await page.goto(`${B}/no-such-page/`);
  expect(res?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("This page doesn't exist");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex");
  await expect(page.locator('link[rel="canonical"]')).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Bank Health overview" })).toHaveAttribute("href", `${B}/`);
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
});

test("built site: no wording that claims a review by AI", () => {
  const walk = (d: string): string[] => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]));
  for (const f of walk("dist").filter((f) => /\.(html|js|json)$/.test(f))) {
    expect(readFileSync(f, "utf-8"), f).not.toMatch(REVIEW_CLAIM);
  }
});
