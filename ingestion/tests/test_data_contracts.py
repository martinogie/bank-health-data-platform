"""Data contracts (D-01): offline tests with small fixture files checked against the real contracts.

Each test writes a gzipped JSON Lines or CSV file to tmp_path, breaks one thing, and checks that the
right finding comes back as an error (the run stops) or a warning (the run goes on).
"""

import csv
import gzip
import json
from datetime import date

import pytest

from ingestion import data_contracts as dc

FIN = dc.load_contract("fdic_financials")
CFPB = dc.load_contract("cfpb_complaints")
CFPB_HEADER = list(CFPB.fields)


def fin_row(cert: int, repdte: str = "20260630", **changes) -> dict:
    row = {}
    for name, rule in FIN.fields.items():
        row[name] = {"int": 100, "number": 1.25, "str": "x"}[rule.type]
    row.update({"CERT": cert, "REPDTE": repdte, "ID": f"{cert}_{repdte}", "IBA": 0, "CBLRIND": 0})
    row.update(changes)
    return row


def write_financials(tmp_path, rows, quarter="20260630"):
    path = tmp_path / "fdic" / "financials" / f"financials_{quarter}.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({"data": r}) + "\n")
    return path


def cfpb_row(i: int, **changes) -> dict:
    row = {
        "Complaint ID": str(1000 + i),
        "Date received": "2026-10-04T07:11:23.000Z",
        "Company": "EXAMPLE BANK",
        "Date sent to company": "2026-10-04T07:16:43.000Z",
        "Issue": "Managing an account",
        "Product": "Checking or savings account",
        "Submitted via": "Web",
        "Company response to consumer": "Closed with explanation",
        "Timely response?": "Yes",
        "Tags": "None",
        "Sub-product": "Checking account",
        "Sub-issue": "None",
        "Company public response": "None",
        "State": "TX",
        "ZIP code": "75201",
    }
    row.update(changes)
    return row


def write_cfpb(tmp_path, rows, header=None):
    path = tmp_path / "cfpb" / "complaints" / "complaints_2026-10-01_2026-10-25.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = header or CFPB_HEADER
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def clean_financials(n=150):
    return [fin_row(1000 + i) for i in range(n)]


# --------------------------------------------------------------------------- the contracts
@pytest.mark.parametrize("source", sorted(set(dc.SOURCE_DIRS.values())))
def test_every_contract_loads_with_valid_levels_and_types(source):
    c = dc.load_contract(source)
    assert c.fields and c.row_id in c.fields
    assert {r.level for r in c.fields.values()} <= set(dc.LEVELS)
    assert any(r.level == "key" for r in c.fields.values())


def test_files_are_matched_to_contracts_by_folder(tmp_path):
    assert (
        dc.source_for(tmp_path / "fdic" / "financials" / "financials_20260630.jsonl.gz") == "fdic_financials"
    )
    assert dc.source_for(tmp_path / "cfpb" / "complaints" / "c.csv.gz") == "cfpb_complaints"
    assert dc.source_for(tmp_path / "other" / "x.gz") is None
    assert dc.financials_quarter(tmp_path / "financials_20260630.jsonl.gz") == date(2026, 6, 30)


def test_a_clean_file_passes(tmp_path):
    res = dc.check_file(write_financials(tmp_path, clean_financials()))
    assert res.ok and res.warnings == [] and res.rows == 150


# --------------------------------------------------------------------------- breaking changes
def test_a_renamed_field_fails_and_the_new_name_warns(tmp_path):
    rows = []
    for r in clean_financials():
        r["ASSETS"] = r.pop("ASSET")
        rows.append(r)
    res = dc.check_file(write_financials(tmp_path, rows))
    assert any("fdic_financials.ASSET is missing (renamed or removed" in e for e in res.errors)
    assert any("new field(s) not in the contract: ASSETS" in w for w in res.warnings)


def test_a_retyped_field_fails(tmp_path):
    rows = [fin_row(1000 + i, ROA="0.85") for i in range(150)]  # number now arrives as text
    res = dc.check_file(write_financials(tmp_path, rows))
    assert any("fdic_financials.ROA should be number, found str x150" in e for e in res.errors)


def test_an_integer_in_a_decimal_field_is_fine(tmp_path):
    rows = [fin_row(1000 + i, ROA=1) for i in range(150)]  # the FDIC sends 1 for 1.0
    assert dc.check_file(write_financials(tmp_path, rows)).ok


def test_a_field_that_comes_back_entirely_null_fails_as_retired(tmp_path):
    rows = [fin_row(1000 + i, NIMY=None) for i in range(150)]
    res = dc.check_file(write_financials(tmp_path, rows))
    assert any("NIMY is null in all 150 rows (retired at the source?)" in e for e in res.errors)


def test_all_null_is_allowed_only_before_the_date_in_the_contract(tmp_path):
    before = [fin_row(1000 + i, repdte="20191231", CBLRIND=None) for i in range(150)]
    assert dc.check_file(write_financials(tmp_path, before, quarter="20191231")).ok  # CBLR began in 2020
    after = [fin_row(1000 + i, CBLRIND=None) for i in range(150)]
    res = dc.check_file(write_financials(tmp_path, after))
    assert any("CBLRIND is null in all" in e for e in res.errors)


def test_a_null_key_fails_and_names_the_rows(tmp_path):
    rows = clean_financials()
    rows[3]["ID"] = None
    res = dc.check_file(write_financials(tmp_path, rows))
    assert any("ID is a key and is null in 1 row(s): 1003" in e for e in res.errors)


def test_a_wrong_quarter_in_a_quarter_file_fails(tmp_path):
    rows = clean_financials()
    rows[0]["REPDTE"] = "20260331"
    res = dc.check_file(write_financials(tmp_path, rows))
    assert any("REPDTE must be '20260630' in every row; found '20260331' x1" in e for e in res.errors)


# --------------------------------------------------------------------------- measures: warn, then fail
def test_one_odd_bank_with_a_null_measure_only_warns_and_is_named(tmp_path):
    rows = [fin_row(1000 + i) for i in range(300)]
    rows[7]["ASSET"] = None  # 1 of 300 = 0.33%, under the 0.5% limit
    res = dc.check_file(write_financials(tmp_path, rows))
    assert res.ok
    assert any("ASSET is null in 1 of 300 rows (0.33%): 1007" in w for w in res.warnings)


def test_a_measure_null_above_half_a_percent_fails(tmp_path):
    rows = [fin_row(1000 + i) for i in range(300)]
    for i in (1, 2):  # 2 of 300 = 0.67%
        rows[i]["ROA"] = None
    res = dc.check_file(write_financials(tmp_path, rows))
    assert any("ROA is null in 2 of 300 rows" in e and "above the 0.5% limit" in e for e in res.errors)


def test_a_warning_names_at_most_20_rows(tmp_path):
    rows = [fin_row(1000 + i) for i in range(10_000)]
    for i in range(25):
        rows[i]["DEP"] = None  # 0.25%: warning
    res = dc.check_file(write_financials(tmp_path, rows))
    (msg,) = [w for w in res.warnings if ".DEP is null" in w]
    assert "1019 and 5 more" in msg and "1020" not in msg


def test_a_jump_in_a_nullable_field_warns(tmp_path):
    rows = [fin_row(1000 + i, ROE=None if i < 30 else 5.0) for i in range(150)]  # 20%, usually <= 0.8%
    res = dc.check_file(write_financials(tmp_path, rows))
    assert res.ok and any("ROE null rate 20.0%, usually up to 0.8%" in w for w in res.warnings)


def test_a_new_code_value_warns(tmp_path):
    rows = [fin_row(1000 + i, IBA=2 if i == 0 else 0) for i in range(150)]
    res = dc.check_file(write_financials(tmp_path, rows))
    assert res.ok and any("IBA: new code value(s) 2 x1" in w for w in res.warnings)


# --------------------------------------------------------------------------- CFPB CSV
def test_a_clean_cfpb_file_passes_and_none_counts_as_null(tmp_path):
    res = dc.check_file(write_cfpb(tmp_path, [cfpb_row(i) for i in range(10)]))
    assert res.ok and res.warnings == []
    assert res.schema["fields"]["Tags"]["null_rate"] == 1.0


def test_a_new_cfpb_product_warns(tmp_path):
    rows = [cfpb_row(i) for i in range(10)] + [cfpb_row(99, Product="Buy now, pay later")]
    res = dc.check_file(write_cfpb(tmp_path, rows))
    assert res.ok and any("Product: new code value(s) 'Buy now, pay later' x1" in w for w in res.warnings)


def test_a_dropped_cfpb_column_fails_even_if_the_file_has_rows(tmp_path):
    header = [h for h in CFPB_HEADER if h != "Company"]
    res = dc.check_file(write_cfpb(tmp_path, [cfpb_row(i) for i in range(10)], header=header))
    assert any("cfpb_complaints.Company is missing" in e for e in res.errors)


def test_a_cfpb_column_that_is_all_none_counts_as_present(tmp_path):
    # small file: "entirely null = retired" needs 100+ rows, so a 10-row month with no tags is fine
    res = dc.check_file(write_cfpb(tmp_path, [cfpb_row(i) for i in range(10)]))
    assert not any("Tags" in e for e in res.errors)


def test_a_cfpb_date_in_a_new_format_fails(tmp_path):
    rows = [cfpb_row(i, **{"Date received": "10/04/2026"}) for i in range(10)]
    res = dc.check_file(write_cfpb(tmp_path, rows))
    assert any("Date received: 10 value(s) not in the expected format" in e for e in res.errors)


def test_a_complaint_without_an_id_fails(tmp_path):
    rows = [cfpb_row(i) for i in range(10)] + [cfpb_row(50, **{"Complaint ID": ""})]
    res = dc.check_file(write_cfpb(tmp_path, rows))
    assert any("Complaint ID is a key and is null in 1 row(s)" in e for e in res.errors)


def test_dbt_and_the_contract_know_the_same_cfpb_products():
    """A product the contract accepts must not turn into 'Unmapped' in dbt, and the other way round."""
    import yaml

    project = dc.CONTRACT_DIR.parent.parent / "dbt" / "dbt_project.yml"
    dbt_products = yaml.safe_load(project.read_text(encoding="utf-8"))["vars"]["cfpb_known_products"]
    assert sorted(dbt_products) == sorted(CFPB.fields["Product"].codes)


# --------------------------------------------------------------------------- the decision
def test_enforce_mode_stops_the_run_and_warn_mode_does_not(tmp_path, capsys):
    bad = dc.check_file(write_financials(tmp_path, [fin_row(1000 + i, NIMY=None) for i in range(150)]))
    with pytest.raises(dc.ContractViolation, match="data contract broken: .*NIMY is null in all"):
        dc.enforce([bad], "enforce")
    summary = dc.enforce([bad], "warn")
    assert summary["errors"] == 1 and summary["would_fail"]
    assert "WOULD FAIL" in capsys.readouterr().out
    assert dc.summary_line(summary).startswith("Data contracts (warn): ") and "would fail" in dc.summary_line(
        summary
    )
    with pytest.raises(ValueError, match="CONTRACTS_MODE"):
        dc.enforce([bad], "off")


def test_a_clean_run_has_no_summary_line(tmp_path):
    ok = dc.check_file(write_financials(tmp_path, clean_financials()))
    assert dc.summary_line(dc.enforce([ok], "enforce")) == ""


def test_the_observed_schema_is_saved_and_diffed_with_the_last_one(tmp_path):
    first = dc.check_file(write_financials(tmp_path, clean_financials()))
    path1, diff1 = dc.save_schema(first, tmp_path / "_schemas", "20261026T110000")
    assert path1.name == "20261026T110000__financials_20260630.json" and diff1 == [
        "first schema saved for this source"
    ]
    rows = []
    for r in clean_financials():
        r["ROA"] = str(r["ROA"])
        r["NEWFIELD"] = 1
        rows.append(r)
    second = dc.check_file(write_financials(tmp_path, rows))
    _, diff2 = dc.save_schema(second, tmp_path / "_schemas", "20261102T110000")
    assert "field added: NEWFIELD" in diff2 and "ROA: types ['float'] -> ['str']" in diff2
    saved = json.loads(path1.read_text(encoding="utf-8"))
    assert saved["rows"] == 150 and saved["fields"]["ASSET"]["types"] == {"int": 150}
