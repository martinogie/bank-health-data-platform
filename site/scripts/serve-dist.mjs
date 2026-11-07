// Serve site/dist under /bankhealth/ the way Cloudflare Pages will: gzip, and the response headers
// from dist/_headers (the "/*" block: CSP and the other security headers). Used by the Playwright
// tests (so a page that breaks the CSP fails them) and by the Lighthouse budget.
//   node scripts/serve-dist.mjs [port]      (default 4321; no dependencies; this computer only)
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { readFileSync, existsSync } from "node:fs";
import { join, extname, normalize } from "node:path";
import { gzipSync } from "node:zlib";

const ROOT = new URL("../dist/", import.meta.url).pathname;
const BASE = "/bankhealth/";
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".ico": "image/x-icon", ".xml": "application/xml", ".txt": "text/plain" };
const COMPRESS = new Set([".html", ".js", ".css", ".json", ".svg", ".xml", ".txt"]);

// The "/*" block of _headers (host-specific blocks, like the pages.dev noindex, don't apply here).
function securityHeaders() {
  const file = join(ROOT, "_headers");
  if (!existsSync(file)) return {};
  const out = {};
  let inAll = false;
  for (const line of readFileSync(file, "utf-8").split("\n")) {
    if (!line.trim() || line.trim().startsWith("#")) continue;
    if (!/^\s/.test(line)) { inAll = line.trim() === "/*"; continue; }
    if (inAll) { const i = line.indexOf(":"); out[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim(); }
  }
  delete out["strict-transport-security"]; // plain http on localhost
  out["content-security-policy"] = (out["content-security-policy"] ?? "").replace(/;\s*upgrade-insecure-requests/, "");
  return out;
}
const SECURITY = securityHeaders();

// Like Pages: an unknown path gets dist/404.html with status 404.
async function notFound(res) {
  try { res.writeHead(404, { ...SECURITY, "content-type": TYPES[".html"] }).end(await readFile(join(ROOT, "404.html"))); }
  catch { res.writeHead(404, SECURITY).end(); }
}

createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (!path.startsWith(BASE)) { res.writeHead(404).end(); return; }
  let file = normalize(join(ROOT, path.slice(BASE.length)));
  if (!file.startsWith(normalize(ROOT))) { res.writeHead(403).end(); return; }
  try { if ((await stat(file)).isDirectory()) file = join(file, "index.html"); } catch { return notFound(res); }
  try {
    let body = await readFile(file);
    const ext = extname(file), headers = { ...SECURITY, "content-type": TYPES[ext] ?? "application/octet-stream" };
    if (COMPRESS.has(ext) && /gzip/.test(req.headers["accept-encoding"] ?? "")) { body = gzipSync(body); headers["content-encoding"] = "gzip"; }
    res.writeHead(200, headers).end(body);
  } catch { notFound(res); }
}).listen(Number(process.argv[2] ?? 4321), "127.0.0.1");
