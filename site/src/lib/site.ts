// Shared client-side helpers. Every number on the site comes from /bankhealth/data/*.json.

export const BANDS: Record<string, { name: string; short: string }> = {
  H: { name: "High", short: "High" },
  E: { name: "Elevated", short: "Elevated" },
  W: { name: "Watch", short: "Watch" },
  L: { name: "Low", short: "Low" },
  D: { name: "Already in distress", short: "In distress" },
  N: { name: "New bank – not scored", short: "Not scored" },
  F: { name: "Office of a foreign bank – not scored", short: "Not scored" },
};

export const BAND_COLOR: Record<string, string> = {
  H: "#8E1B2E", E: "#B14A14", W: "#D9A23A", L: "#3E5C73", D: "#2B2420",
};

export function base(): string {
  return document.body.dataset.base || "";
}

export function dataUrl(path: string): string {
  return `${base()}/data/${path}`;
}

export function bankUrl(cert: number): string {
  return `${base()}/bank/?cert=${cert}`;
}

const cache = new Map<string, Promise<any>>();
export function loadJson<T = any>(path: string): Promise<T> {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(dataUrl(path)).then((r) => {
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r.json();
      }),
    );
  }
  return cache.get(path)!;
}

export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export const fmtInt = (n: number | null | undefined) => (n == null ? "–" : n.toLocaleString("en-US"));
export const pct0 = (p: number | null | undefined) => (p == null ? "–" : `${Math.round(p * 100)}%`);
export const fixed = (v: number | null | undefined, d = 2) => (v == null ? "–" : v.toFixed(d));

export function money(m: number | null | undefined): string {
  if (m == null) return "–";
  if (m >= 1000) return `$${(m / 1000).toFixed(1)}bn`;
  if (m >= 1) return `$${Math.round(m)}m`;
  return `$${(m * 1000).toFixed(0)}k`;
}

export function quarterText(o: { dataAsOf: { quarterLabel: string; updated: string } }): string {
  const d = new Date(`${o.dataAsOf.updated}T12:00:00Z`);
  const updated = d.toLocaleDateString("en-US", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  return `Data as of ${o.dataAsOf.quarterLabel} · updated ${updated}`;
}

export function bandChip(code: string): string {
  return `<span class="chip ${esc(code)}">${esc(BANDS[code]?.short ?? code)}</span>`;
}

export function driverChip(d: string | null | undefined): string {
  if (!d) return "";
  return `<span class="driver ${esc(d)}">${d === "credit" ? "Credit" : "Earnings"}</span>`;
}

// Watchlist: kept in localStorage (when the browser allows it) and in ?certs= links.
const KEY = "bankhealth.watchlist";
export function readWatchlist(): number[] {
  const fromUrl = new URLSearchParams(location.search).get("certs");
  if (fromUrl) return parseCerts(fromUrl);
  try {
    return parseCerts(localStorage.getItem(KEY) || "");
  } catch {
    return [];
  }
}
export function saveWatchlist(certs: number[]): void {
  try {
    localStorage.setItem(KEY, certs.join(","));
  } catch {
    /* storage blocked: the page link still holds the list */
  }
  const url = new URL(location.href);
  if (certs.length) url.searchParams.set("certs", certs.join(","));
  else url.searchParams.delete("certs");
  history.replaceState(null, "", url);
}
function parseCerts(s: string): number[] {
  return [...new Set(s.split(",").map((x) => parseInt(x, 10)).filter((n) => Number.isFinite(n) && n > 0))];
}

// Sparkline points for an SVG of 200 x 56 (nulls skipped).
export function sparkPoints(values: (number | null)[]): { points: string; last?: [number, number] } {
  const v = values.map((x, i) => [i, x] as const).filter(([, x]) => x != null) as [number, number][];
  if (v.length < 2) return { points: "" };
  const ys = v.map(([, y]) => y);
  const min = Math.min(...ys), max = Math.max(...ys), span = max - min || 1;
  const pts = v.map(([i, y]) => [Math.round(6 + i * (188 / (values.length - 1))), Math.round(48 - ((y - min) / span) * 40)] as [number, number]);
  return { points: pts.map((p) => p.join(",")).join(" "), last: pts[pts.length - 1] };
}

export function downloadCsv(name: string, rows: (string | number | null)[][]): void {
  const text = rows.map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
