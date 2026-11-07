// Prove a project's pinned design copy is exactly the released version named in DESIGN.json.
// Copied next to the design files by pin-design.mjs; run in the project's CI:
//   node src/design/check-design.mjs
// Exit 1 if any file was edited, added to the manifest by hand, or is missing. To change the
// design, change it in portfolio-site, release a new version and pin it again; never edit here.
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = dirname(fileURLToPath(import.meta.url));
const manifest = JSON.parse(readFileSync(join(dir, "DESIGN.json"), "utf-8"));
const bad = [];
for (const { path: file, sha256: want } of manifest.files) {
  const p = join(dir, file);
  if (!existsSync(p)) { bad.push(`${file}: missing`); continue; }
  const got = createHash("sha256").update(readFileSync(p)).digest("hex");
  if (got !== want) bad.push(`${file}: changed since ${manifest.tag}`);
}
if (!/^design-v\d+\.\d+\.\d+$/.test(manifest.tag) || manifest.tag !== `design-v${manifest.version}`) bad.push(`DESIGN.json: bad tag ${manifest.tag}`);
if (bad.length) {
  console.error(`design copy does not match ${manifest.tag}:\n  ${bad.join("\n  ")}`);
  process.exit(1);
}
console.log(`design copy matches ${manifest.tag} (${manifest.sector}), ${manifest.files.length} files`);
