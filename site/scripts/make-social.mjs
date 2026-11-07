// Render the social preview image (public/social.png, 1200x630) from an HTML card with the site's
// own fonts and colours. Run once when the wording changes (in the Playwright image):
//   node scripts/make-social.mjs
import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";

const font = (p) => `data:font/woff2;base64,${readFileSync(new URL(`../node_modules/${p}`, import.meta.url)).toString("base64")}`;
const serif = font("@fontsource-variable/newsreader/files/newsreader-latin-wght-normal.woff2");
const sans = font("@fontsource-variable/inter/files/inter-latin-wght-normal.woff2");

const html = `<!doctype html><html><head><style>
@font-face { font-family: Newsreader; src: url(${serif}); font-weight: 200 800; }
@font-face { font-family: Inter; src: url(${sans}); font-weight: 100 900; }
body { margin: 0; width: 1200px; height: 630px; background: #F6F1E6; font-family: Inter; color: #1A1612; }
.card { box-sizing: border-box; width: 1200px; height: 630px; padding: 72px 80px; border-top: 14px solid #8E1B2E;
        display: flex; flex-direction: column; justify-content: space-between; }
.eyebrow { font-size: 22px; letter-spacing: 0.12em; text-transform: uppercase; color: #8E1B2E; font-weight: 600; }
h1 { font-family: Newsreader; font-weight: 500; font-size: 112px; line-height: 1; margin: 18px 0 0; letter-spacing: -0.02em; }
p { font-family: Newsreader; font-size: 36px; line-height: 1.3; color: #3D352C; margin: 28px 0 0; max-width: 980px; }
.foot { display: flex; justify-content: space-between; font-size: 22px; color: #5B5246; border-top: 2px solid #1A1612; padding-top: 22px; }
</style></head><body><div class="card">
<div><div class="eyebrow">US bank early warning</div><h1>Bank Health</h1>
<p>Scores 4,000+ FDIC-insured banks each week on their chance of distress in the next year, with the reasons behind every score.</p></div>
<div class="foot"><span>finance.martinsogiekpolor.com/bankhealth</span><span>Public FDIC data · not a supervisory rating</span></div>
</div></body></html>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
await page.setContent(html, { waitUntil: "load" });
await page.evaluate(() => document.fonts.ready);
await page.screenshot({ path: new URL("../public/social.png", import.meta.url).pathname });
await browser.close();
console.log("wrote public/social.png (1200x630)");
