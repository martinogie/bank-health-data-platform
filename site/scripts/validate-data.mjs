// Validate every site data file against its JSON Schema (site/data/schemas/). Exit 1 on any error.
//   node scripts/validate-data.mjs [data dir]     (default: public/data)
import Ajv2020 from "ajv/dist/2020.js";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join } from "node:path";

const dir = process.argv[2] ?? "public/data";
const load = (p) => JSON.parse(readFileSync(p, "utf-8"));
const ajv = new Ajv2020({ allErrors: true, strict: false });
ajv.addSchema(load("data/schemas/common.schema.json"));
ajv.addSchema(load("data/schemas/files.schema.json"));
const check = (def, file) => {
  const ok = ajv.validate(`files.schema.json#/$defs/${def}`, load(file));
  if (!ok) console.error(`${file}:`, ajv.errorsText(ajv.errors, { separator: "; " }));
  return ok ? 0 : 1;
};

let errors = 0, count = 0;
for (const [def, name] of [["overview", "overview.json"], ["banksIndex", "banks_index.json"], ["model", "model.json"], ["pipeline", "pipeline.json"]]) {
  const f = join(dir, name);
  if (!existsSync(f)) { console.error(`missing ${f}`); errors++; continue; }
  errors += check(def, f); count++;
}
for (const f of readdirSync(join(dir, "banks"))) { errors += check("bank", join(dir, "banks", f)); count++; }
console.log(`${count} data files checked, ${errors} invalid`);
process.exit(errors ? 1 : 0);
