// Serve site/dist under /bankhealth/ with gzip, the way Cloudflare Pages serves it, for Lighthouse.
//   node scripts/serve-dist.mjs [port]      (default 4321; no dependencies)
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, extname, normalize } from "node:path";
import { gzipSync } from "node:zlib";

const ROOT = new URL("../dist/", import.meta.url).pathname;
const BASE = "/bankhealth/";
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".ico": "image/x-icon" };
const COMPRESS = new Set([".html", ".js", ".css", ".json", ".svg"]);

createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (!path.startsWith(BASE)) { res.writeHead(404).end(); return; }
  let file = normalize(join(ROOT, path.slice(BASE.length)));
  if (!file.startsWith(normalize(ROOT))) { res.writeHead(403).end(); return; }
  try { if ((await stat(file)).isDirectory()) file = join(file, "index.html"); } catch { res.writeHead(404).end(); return; }
  try {
    let body = await readFile(file);
    const ext = extname(file), headers = { "content-type": TYPES[ext] ?? "application/octet-stream" };
    if (COMPRESS.has(ext) && /gzip/.test(req.headers["accept-encoding"] ?? "")) { body = gzipSync(body); headers["content-encoding"] = "gzip"; }
    res.writeHead(200, headers).end(body);
  } catch { res.writeHead(404).end(); }
}).listen(Number(process.argv[2] ?? 4321), "127.0.0.1");
