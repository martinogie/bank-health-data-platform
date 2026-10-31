"""Data contracts: check each downloaded source file against its contract before it reaches bronze.

A contract (ingestion/contracts/<source>.yml) lists the fields a source must have, their types,
formats, allowed nulls and code lists. `check_file` reads a file once and returns errors (the run
must stop) and warnings (logged, listed in the success message):

- errors:   a field missing or entirely null (the FDIC returns a retired field as all nulls), a type
            change, a wrong format, a null in a key field, more than 0.5% nulls in a measure, an
            unexpected value where one is required (REPDTE must be the quarter requested)
- warnings: a field the contract doesn't know, a new code value, some nulls in a measure (naming
            up to 20 rows), a null rate far above usual in a nullable field

Each check also returns the observed schema (fields, types, null rates, code counts), saved per
run so changes can be diffed. Pure Python and offline: the unit tests use small fixture files.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# No third-party import at module level: importing this module must never fail, so that warn
# mode can stay fail-open (PyYAML is imported in load_contract).

CONTRACT_DIR = Path(__file__).with_name("contracts")
LEVELS = ("key", "measure", "nullable", "info")
TYPES = ("int", "number", "str")
MEASURE_FAIL_RATE = 0.005  # a measure fails above 0.5% null rows; one odd bank only warns
ALL_NULL_MIN_ROWS = 100  # "entirely null = retired" is judged only on files this size or larger
NAMED_ROWS = 20  # warnings name at most this many rows
MODES = ("enforce", "warn")

# Raw folder (relative to data/raw) -> contract name
SOURCE_DIRS = {
    ("fdic", "financials"): "fdic_financials",
    ("fdic", "institutions"): "fdic_institutions",
    ("fdic", "failures"): "fdic_failures",
    ("cfpb", "complaints"): "cfpb_complaints",
}


class ContractViolation(Exception):
    """A source file broke its contract; nothing from this run may go to bronze."""


@dataclass(frozen=True)
class FieldRule:
    name: str
    type: str
    level: str
    pattern: str | None = None
    codes: tuple[Any, ...] | None = None
    usual_null_rate: float = 0.0
    empty_before: date | None = None  # entirely null is normal for files before this date


@dataclass(frozen=True)
class Contract:
    source: str
    format: str  # jsonl | csv
    row_id: str
    fields: dict[str, FieldRule]
    null_tokens: tuple[str, ...] = ()


@dataclass
class Result:
    source: str
    file: str
    rows: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- contracts
def load_contract(source: str, contract_dir: Path = CONTRACT_DIR) -> Contract:
    import yaml  # here, not at module level: see the note at the top

    spec = yaml.safe_load((contract_dir / f"{source}.yml").read_text(encoding="utf-8"))
    fields = {}
    for name, f in spec["fields"].items():
        if f["type"] not in TYPES or f["level"] not in LEVELS:
            raise ValueError(f"{source}.{name}: unknown type or level {f}")
        empty_before = f.get("empty_before")
        fields[name] = FieldRule(
            name=name,
            type=f["type"],
            level=f["level"],
            pattern=f.get("pattern"),
            codes=tuple(f["codes"]) if "codes" in f else None,
            usual_null_rate=float(f.get("usual_null_rate", 0.0)),
            empty_before=date.fromisoformat(str(empty_before)) if empty_before else None,
        )
    if spec["row_id"] not in fields:
        raise ValueError(f"{source}: row_id {spec['row_id']!r} is not a contract field")
    return Contract(
        source=spec["source"],
        format=spec["format"],
        row_id=spec["row_id"],
        fields=fields,
        null_tokens=tuple(spec.get("null_tokens", ())),
    )


def source_for(path: Path) -> str | None:
    """Contract name for a raw file, from its folder (e.g. .../fdic/financials/x.jsonl.gz)."""
    return SOURCE_DIRS.get((path.parent.parent.name, path.parent.name))


def financials_quarter(path: Path) -> date | None:
    """financials_20260630.jsonl.gz -> date(2026, 6, 30); None for other files."""
    m = re.fullmatch(r"financials_(\d{8})\.jsonl\.gz", path.name)
    return datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


# --------------------------------------------------------------------------- reading
def read_records(path: Path, fmt: str) -> Iterator[dict[str, Any]]:
    """Rows of a gzipped JSON Lines or CSV file, as dicts. FDIC rows may be wrapped in {"data": ...}."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        if fmt == "csv":
            yield from csv.DictReader(fh)
            return
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                yield rec.get("data", rec) if isinstance(rec.get("data"), dict) else rec


def csv_header(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return next(csv.reader(fh), [])


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _type_ok(rule_type: str, observed: str) -> bool:
    if rule_type == "number":
        return observed in ("int", "float")
    return observed == rule_type


# --------------------------------------------------------------------------- checking
def check_records(
    records: Iterable[dict[str, Any]],
    contract: Contract,
    file_label: str,
    *,
    expected: dict[str, Any] | None = None,
    as_of: date | None = None,
    header: list[str] | None = None,
) -> Result:
    """Check rows against the contract. `expected` = field -> the one value every row must have.
    `as_of` = the date the file describes (for `empty_before`). `header` = CSV columns, so a column
    is "present" even if every row is null."""
    expected = expected or {}
    present: set[str] = set(header or [])
    types: dict[str, Counter] = {}
    nulls: Counter = Counter()
    null_ids: dict[str, list[Any]] = {}
    bad_type: dict[str, Counter] = {}
    bad_type_ids: dict[str, list[Any]] = {}
    bad_pattern: Counter = Counter()
    bad_pattern_examples: dict[str, list[Any]] = {}
    unexpected: dict[str, Counter] = {}
    codes: dict[str, Counter] = {
        name: Counter() for name, r in contract.fields.items() if r.codes is not None
    }
    patterns = {n: re.compile(r.pattern) for n, r in contract.fields.items() if r.pattern}
    rows = 0
    for rec in records:
        rows += 1
        row_id = rec.get(contract.row_id)
        for name, value in rec.items():
            present.add(name)
            is_null = value is None or (isinstance(value, str) and value in contract.null_tokens)
            t = "null" if is_null else _type_of(value)
            types.setdefault(name, Counter())[t] += 1
            rule = contract.fields.get(name)
            if rule is None:
                continue
            if is_null:
                nulls[name] += 1
                ids = null_ids.setdefault(name, [])
                if len(ids) < NAMED_ROWS:
                    ids.append(row_id)
                continue
            if not _type_ok(rule.type, t):
                bad_type.setdefault(name, Counter())[t] += 1
                ids = bad_type_ids.setdefault(name, [])
                if len(ids) < 3:
                    ids.append(row_id)
                continue
            if name in patterns and not patterns[name].search(str(value)):
                bad_pattern[name] += 1
                examples = bad_pattern_examples.setdefault(name, [])
                if len(examples) < 3:
                    examples.append(value)
            if name in codes:
                codes[name][value] += 1
            if name in expected and value != expected[name]:
                unexpected.setdefault(name, Counter())[value] += 1

    res = Result(source=contract.source, file=file_label, rows=rows)
    res.schema = {
        "source": contract.source,
        "file": file_label,
        "rows": rows,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "fields": {
            name: {
                "types": dict(sorted(types.get(name, Counter()).items())),
                "null_rate": round(nulls[name] / rows, 6) if rows and name in contract.fields else None,
                **({"codes": {str(k): v for k, v in codes[name].most_common()}} if name in codes else {}),
            }
            for name in sorted(present)
        },
    }
    if rows == 0:
        res.warnings.append(f"{file_label}: no rows; nothing to check")
        return res

    unknown = sorted(present - set(contract.fields))
    if unknown:
        res.warnings.append(f"{file_label}: new field(s) not in the contract: {', '.join(unknown)}")

    for name, rule in contract.fields.items():
        where = f"{file_label}: {contract.source}.{name}"
        n_null = nulls[name]
        rate = n_null / rows
        if name not in present:
            msg = f"{where} is missing (renamed or removed at the source?)"
            (res.warnings if rule.level == "info" else res.errors).append(msg)
            continue
        if rule.level != "info" and n_null == rows and rows >= ALL_NULL_MIN_ROWS:
            if not (rule.empty_before and as_of and as_of < rule.empty_before):
                res.errors.append(f"{where} is null in all {rows:,} rows (retired at the source?)")
                continue
        if name in bad_type:
            seen = ", ".join(f"{t} x{c}" for t, c in bad_type[name].most_common())
            msg = f"{where} should be {rule.type}, found {seen} (rows {bad_type_ids[name]})"
            (res.warnings if rule.level == "info" else res.errors).append(msg)
        if bad_pattern[name]:
            res.errors.append(
                f"{where}: {bad_pattern[name]:,} value(s) not in the expected format {rule.pattern!r}, "
                f"e.g. {bad_pattern_examples[name]}"
            )
        if name in unexpected:
            got = ", ".join(f"{v!r} x{c}" for v, c in unexpected[name].most_common(3))
            res.errors.append(f"{where} must be {expected[name]!r} in every row; found {got}")
        if n_null and rule.level == "key":
            res.errors.append(
                f"{where} is a key and is null in {n_null:,} row(s): {_ids(null_ids[name], n_null)}"
            )
        elif n_null and rule.level == "measure":
            msg = (
                f"{where} is null in {n_null:,} of {rows:,} rows ({rate:.2%}): {_ids(null_ids[name], n_null)}"
            )
            if rate > MEASURE_FAIL_RATE:
                res.errors.append(f"{msg}; above the {MEASURE_FAIL_RATE:.1%} limit")
            else:
                res.warnings.append(msg)
        elif rule.level == "nullable" and n_null < rows:
            usual = rule.usual_null_rate
            if rate > max(2 * usual, usual + 0.05):
                res.warnings.append(f"{where} null rate {rate:.1%}, usually up to {usual:.1%}")
        if rule.codes is not None and name in codes:
            new = {v: c for v, c in codes[name].items() if v not in rule.codes}
            if new:
                listed = ", ".join(f"{v!r} x{c:,}" for v, c in sorted(new.items(), key=lambda x: -x[1])[:10])
                res.warnings.append(f"{where}: new code value(s) {listed}")
    return res


def _ids(ids: list[Any], total: int) -> str:
    """'35541, 1022 and 3 more': the rows named in a finding (row ids: CERTs, complaint ids)."""
    more = f" and {total - len(ids):,} more" if total > len(ids) else ""
    return ", ".join(str(i) for i in ids) + more


def check_file(path: Path, contract: Contract | None = None, *, as_of: date | None = None) -> Result:
    """Check one downloaded file. FDIC quarter files must contain only the quarter in their name."""
    path = Path(path)
    if contract is None:
        source = source_for(path)
        if source is None:
            raise ValueError(f"no contract for {path}")
        contract = load_contract(source)
    expected: dict[str, Any] = {}
    quarter = financials_quarter(path) if contract.source == "fdic_financials" else None
    if quarter:
        expected["REPDTE"] = quarter.strftime("%Y%m%d")
        as_of = quarter
    header = csv_header(path) if contract.format == "csv" else None
    return check_records(
        read_records(path, contract.format),
        contract,
        path.name,
        expected=expected,
        as_of=as_of or date.today(),
        header=header,
    )


# --------------------------------------------------------------------------- schema history
def schema_diff(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """What changed in the observed schema since the last saved one for this source."""
    if not old:
        return ["first schema saved for this source"]
    of, nf = old.get("fields", {}), new.get("fields", {})
    out = [f"field added: {n}" for n in sorted(set(nf) - set(of))]
    out += [f"field removed: {n}" for n in sorted(set(of) - set(nf))]
    for n in sorted(set(of) & set(nf)):
        ot = set(of[n].get("types", {})) - {"null"}
        nt = set(nf[n].get("types", {})) - {"null"}
        if ot != nt:
            out.append(f"{n}: types {sorted(ot)} -> {sorted(nt)}")
        a, b = of[n].get("null_rate"), nf[n].get("null_rate")
        if a is not None and b is not None and abs(b - a) >= 0.05:
            out.append(f"{n}: null rate {a:.1%} -> {b:.1%}")
    return out


def save_schema(result: Result, schema_dir: Path, run_stamp: str) -> tuple[Path, list[str]]:
    """Write the observed schema to <schema_dir>/<source>/<run_stamp>__<file>.json and diff it with
    the previous one for the same source. Returns (path, diff lines)."""
    folder = Path(schema_dir) / result.source
    folder.mkdir(parents=True, exist_ok=True)
    stem = result.file.split(".")[0]
    previous = sorted(folder.glob("*.json"))
    old = json.loads(previous[-1].read_text(encoding="utf-8")) if previous else None
    path = folder / f"{run_stamp}__{stem}.json"
    path.write_text(json.dumps(result.schema, indent=1, sort_keys=True, default=str), encoding="utf-8")
    return path, schema_diff(old, result.schema)


# --------------------------------------------------------------------------- decision
def enforce(results: list[Result], mode: str) -> dict[str, Any]:
    """Print every finding; in `enforce` mode raise ContractViolation if any file has errors.
    In `warn` mode errors are printed as "WOULD FAIL" and the run continues."""
    if mode not in MODES:
        raise ValueError(f"CONTRACTS_MODE must be one of {MODES}, not {mode!r}")
    errors = [e for r in results for e in r.errors]
    warnings = [w for r in results for w in r.warnings]
    for r in results:
        status = "ok" if r.ok else ("FAILED" if mode == "enforce" else "WOULD FAIL")
        print(f"contract {r.source} {r.file}: {r.rows:,} rows, {status}")
    for e in errors:
        print(("ERROR " if mode == "enforce" else "WOULD FAIL ") + e)
    for w in warnings:
        print("WARN " + w)
    summary = {
        "mode": mode,
        "files": len(results),
        "errors": len(errors),
        "warnings": warnings[:50],
        "warning_count": len(warnings),
        "would_fail": errors[:20] if mode == "warn" else [],
    }
    if errors and mode == "enforce":
        first = errors[0] if len(errors) == 1 else f"{errors[0]} (+{len(errors) - 1} more, see the task log)"
        raise ContractViolation(f"data contract broken: {first}")
    return summary


def _checker_error(where: str, exc: BaseException) -> str:
    return f"contract checker error ({where}): {type(exc).__name__}: {str(exc)[:300]}; not checked"


def run_checks(paths: Iterable[Path], mode: str, schema_dir: Path, run_stamp: str) -> dict[str, Any]:
    """The check_contracts task: check each file that has a contract, save its schema, decide.

    warn mode is FAIL-OPEN: an exception inside the checker itself (an unreadable file, a broken
    contract, a missing library, a full disk, a bug) is reported as a warning and never raises, so
    the run carries on as if no checker existed. Only enforce mode can stop a run: a broken
    contract raises ContractViolation, and so does a checker error, because a file that couldn't
    be checked can't be let through. An unknown mode counts as warn, with a warning.
    Always returns 'schema_files' (the schema files saved, for upload)."""
    checker_warnings: list[str] = []
    schema_files: list[str] = []
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        checker_warnings.append(f"CONTRACTS_MODE={mode!r} is not one of {MODES}; running as warn")
        mode = "warn"
    try:
        results: list[Result] = []
        for path in (Path(p) for p in paths):
            try:
                if source_for(path) is None:
                    continue
                res = check_file(path)
            except Exception as exc:
                msg = _checker_error(path.name, exc)
                if mode == "enforce":
                    raise ContractViolation(f"data contract could not be checked: {msg}") from exc
                checker_warnings.append(msg)
                continue
            results.append(res)
            try:
                saved, diff = save_schema(res, schema_dir, run_stamp)
                schema_files.append(str(saved))
                print(f"schema {res.source} {res.file}: " + "; ".join(diff[:10]))
            except Exception as exc:  # the schema history is a record, never a reason to stop
                checker_warnings.append(_checker_error(f"saving the schema of {path.name}", exc))
        summary = enforce(results, mode)
    except Exception as exc:
        if mode == "enforce":
            if isinstance(exc, ContractViolation):
                raise
            raise ContractViolation(
                f"data contract could not be checked: {_checker_error('run', exc)}"
            ) from exc
        checker_warnings.append(_checker_error("run", exc))
        summary = {
            "mode": mode,
            "files": 0,
            "errors": 0,
            "warnings": [],
            "warning_count": 0,
            "would_fail": [],
        }
    for w in checker_warnings:
        print("WARN " + w)
    summary["warnings"] = (checker_warnings + summary["warnings"])[:50]
    summary["warning_count"] += len(checker_warnings)
    summary["schema_files"] = schema_files
    return summary


def summary_line(summary: dict[str, Any] | None) -> str:
    """One line for the success message ('' when there is nothing to say)."""
    if not summary:
        return ""
    parts = []
    if summary.get("warning_count"):
        parts.append(f"{summary['warning_count']} warning(s), first: {summary['warnings'][0]}")
    if summary.get("would_fail"):
        parts.append(f"{len(summary['would_fail'])} would fail in enforce mode")
    return f"Data contracts ({summary['mode']}): " + "; ".join(parts) if parts else ""
